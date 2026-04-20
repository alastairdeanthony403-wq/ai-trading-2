# ============================================================
# AI Trading Engine (UNIFIED BOT LOGIC + TRUE BACKTESTER)
# ============================================================

from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import pandas as pd
import requests
import uuid
import sqlite3
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)

# ---------------- CONFIG ----------------
bot_config = {
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "risk_reward": 2,
    "risk_percent": 1,
    "min_confidence": 60
}

DB_NAME = "trades.db"

BINANCE_BASE_URLS = [
    "https://api.binance.com",
    "https://api-gcp.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
    "https://data-api.binance.vision",
]

COINBASE_PRODUCT_MAP = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "BNBUSDT": "BNB-USD",
    "SOLUSDT": "SOL-USD",
}

COINBASE_GRANULARITY_MAP = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 3600,  # fetch 1h and aggregate to 4h
}


# ---------------- DATABASE ----------------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id TEXT,
        symbol TEXT,
        type TEXT,
        entry REAL,
        sl REAL,
        tp REAL,
        size REAL,
        exit REAL,
        pnl REAL,
        status TEXT,
        time TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS alerts (
        id TEXT,
        message TEXT,
        time TEXT
    )
    """)

    conn.commit()
    conn.close()


init_db()


# ---------------- DATA HELPERS ----------------
def _request_json(url, params=None, timeout=10):
    return requests.get(url, params=params, timeout=timeout)


def _fetch_binance_klines(symbol, interval="1m", limit=100):
    last_error = None

    for base_url in BINANCE_BASE_URLS:
        try:
            response = _request_json(
                f"{base_url}/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "limit": limit
                },
                timeout=8
            )

            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) >= 2:
                    return data
                last_error = f"{base_url} returned invalid kline data"
                continue

            body = (response.text or "").strip()
            if len(body) > 250:
                body = body[:250] + "..."
            last_error = f"{base_url} returned HTTP {response.status_code}: {body}"

        except requests.exceptions.RequestException as e:
            last_error = f"{base_url} request failed: {str(e)}"

    raise RuntimeError(last_error or "All Binance endpoints failed")


def _coinbase_fetch_candles(product_id, granularity, total_needed):
    all_rows = []
    end_time = datetime.now(timezone.utc)

    while len(all_rows) < total_needed:
        remaining = total_needed - len(all_rows)
        batch_size = min(300, remaining)

        start_time = end_time - timedelta(seconds=granularity * batch_size)

        response = _request_json(
            f"https://api.exchange.coinbase.com/products/{product_id}/candles",
            params={
                "granularity": granularity,
                "start": start_time.isoformat(),
                "end": end_time.isoformat()
            },
            timeout=12
        )

        if response.status_code != 200:
            body = (response.text or "").strip()
            if len(body) > 250:
                body = body[:250] + "..."
            raise RuntimeError(f"Coinbase returned HTTP {response.status_code}: {body}")

        rows = response.json()

        if not isinstance(rows, list):
            raise RuntimeError(f"Coinbase returned invalid candle data: {rows}")

        if not rows:
            break

        all_rows.extend(rows)

        earliest_ts = min(r[0] for r in rows)
        end_time = datetime.fromtimestamp(earliest_ts, tz=timezone.utc) - timedelta(seconds=granularity)

        if len(rows) < batch_size:
            break

    if not all_rows:
        raise RuntimeError("Coinbase returned no candle data")

    unique_rows = {}
    for row in all_rows:
        if isinstance(row, list) and len(row) >= 6:
            unique_rows[int(row[0])] = row

    ordered = [unique_rows[k] for k in sorted(unique_rows.keys())]
    return ordered[-total_needed:]


def _aggregate_coinbase_1h_to_4h(rows, limit):
    if not rows:
        return []

    rows = sorted(rows, key=lambda x: x[0])
    grouped = []
    bucket = []

    for row in rows:
        bucket.append(row)
        if len(bucket) == 4:
            ts = int(bucket[0][0])
            low = min(float(r[1]) for r in bucket)
            high = max(float(r[2]) for r in bucket)
            open_price = float(bucket[0][3])
            close_price = float(bucket[-1][4])
            volume = sum(float(r[5]) for r in bucket)

            grouped.append([ts, low, high, open_price, close_price, volume])
            bucket = []

    return grouped[-limit:]


def _fetch_coinbase_raw(symbol="BTCUSDT", interval="5m", limit=200):
    product_id = COINBASE_PRODUCT_MAP.get(symbol)
    if not product_id:
        raise RuntimeError(f"No Coinbase fallback mapping for symbol {symbol}")

    if interval not in COINBASE_GRANULARITY_MAP:
        raise RuntimeError(f"No Coinbase fallback granularity for interval {interval}")

    if interval == "4h":
        raw_1h = _coinbase_fetch_candles(product_id, 3600, max(limit * 4, 4))
        aggregated = _aggregate_coinbase_1h_to_4h(raw_1h, limit)

        if not aggregated:
            raise RuntimeError("Coinbase fallback could not build 4h candles")

        converted = []
        for row in aggregated:
            converted.append([
                int(row[0]) * 1000,
                str(row[3]),  # open
                str(row[2]),  # high
                str(row[1]),  # low
                str(row[4]),  # close
                str(row[5]),  # volume
            ])
        return converted

    granularity = COINBASE_GRANULARITY_MAP[interval]
    rows = _coinbase_fetch_candles(product_id, granularity, limit)

    converted = []
    for row in rows:
        converted.append([
            int(row[0]) * 1000,
            str(row[3]),  # open
            str(row[2]),  # high
            str(row[1]),  # low
            str(row[4]),  # close
            str(row[5]),  # volume
        ])

    return converted


def fetch_binance_raw(symbol="BTCUSDT", interval="5m", limit=500):
    if not symbol or not symbol.endswith("USDT"):
        raise ValueError("Invalid symbol")

    if limit < 1:
        raise ValueError("Invalid candle limit")

    binance_error = None

    try:
        return _fetch_binance_klines(symbol, interval=interval, limit=limit)
    except Exception as e:
        binance_error = str(e)

    try:
        return _fetch_coinbase_raw(symbol=symbol, interval=interval, limit=limit)
    except Exception as fallback_error:
        raise RuntimeError(
            f"Primary source failed ({binance_error}) | Fallback source failed ({fallback_error})"
        )


def raw_candles_to_df(raw_candles):
    if not raw_candles or len(raw_candles) < 2:
        return None

    first_row = raw_candles[0]

    if len(first_row) >= 12:
        df = pd.DataFrame(raw_candles, columns=[
            "time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])
    elif len(first_row) >= 6:
        df = pd.DataFrame(raw_candles, columns=[
            "time", "open", "high", "low", "close", "volume"
        ])
    else:
        return None

    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["time"] = pd.to_datetime(df["time"], unit="ms", errors="coerce")
    df.dropna(subset=["time", "open", "high", "low", "close", "volume"], inplace=True)

    if len(df) < 2:
        return None

    return df.reset_index(drop=True)


def fetch_binance(symbol, interval="1m", limit=100):
    try:
        raw = fetch_binance_raw(symbol=symbol, interval=interval, limit=limit)
        return raw_candles_to_df(raw)
    except Exception:
        return None


# ---------------- CORE BOT LOGIC ----------------
def generate_signal(df):
    if df is None or len(df) < 2:
        return "HOLD"

    latest = df.iloc[-1]["close"]
    previous = df.iloc[-2]["close"]

    if latest > previous:
        return "BUY"
    elif latest < previous:
        return "SELL"
    return "HOLD"


def get_structure(df):
    if df is None or len(df) < 20:
        return "Range / Mixed"

    closes = df["close"]
    sma20 = closes.tail(20).mean()

    c0 = closes.iloc[-1]
    c1 = closes.iloc[-2]
    c2 = closes.iloc[-3]

    if c0 > sma20 and c0 > c1 > c2:
        return "Bullish Structure"
    elif c0 < sma20 and c0 < c1 < c2:
        return "Bearish Structure"
    return "Range / Mixed"


def get_market_regime(df):
    if df is None or len(df) < 20:
        return "Unknown"

    recent_high = df["high"].tail(20).max()
    recent_low = df["low"].tail(20).min()
    avg_price = df["close"].tail(20).mean()

    if avg_price == 0:
        return "Unknown"

    range_percent = ((recent_high - recent_low) / avg_price) * 100

    if range_percent > 2.5:
        return "Trending"
    elif range_percent > 1.0:
        return "Active"
    return "Range / Quiet"


def estimate_confidence(df, signal):
    if df is None or len(df) < 20:
        return 50

    closes = df["close"]
    latest = closes.iloc[-1]
    previous = closes.iloc[-2]
    sma20 = closes.tail(20).mean()
    sma5 = closes.tail(5).mean()

    confidence = 50

    if signal == "BUY":
        if latest > sma20:
            confidence += 15
        if latest > previous:
            confidence += 10
        if latest > sma5:
            confidence += 10

    elif signal == "SELL":
        if latest < sma20:
            confidence += 15
        if latest < previous:
            confidence += 10
        if latest < sma5:
            confidence += 10

    return max(35, min(95, confidence))


def get_bias_from_signal(signal):
    if signal == "BUY":
        return "Bullish"
    elif signal == "SELL":
        return "Bearish"
    return "Neutral"


def get_trade_idea(signal):
    if signal == "BUY":
        return "Pullback long / continuation"
    elif signal == "SELL":
        return "Reject highs / continuation short"
    return "Wait for clearer confirmation"


def evaluate_bot_window(df, strategy="bot"):
    if df is None or len(df) < 20:
        return {
            "signal": "HOLD",
            "bias": "Neutral",
            "structure": "Range / Mixed",
            "regime": "Unknown",
            "confidence": 50,
            "trade_idea": "Not enough data"
        }

    latest_close = float(df.iloc[-1]["close"])
    latest_open = float(df.iloc[-1]["open"])
    latest_high = float(df.iloc[-1]["high"])
    latest_low = float(df.iloc[-1]["low"])
    prev_close = float(df.iloc[-2]["close"])

    raw_signal = generate_signal(df)
    structure = get_structure(df)
    regime = get_market_regime(df)
    confidence = estimate_confidence(df, raw_signal)
    bias = get_bias_from_signal(raw_signal)
    trade_idea = get_trade_idea(raw_signal)

    final_signal = "HOLD"

    if strategy == "basic":
        final_signal = raw_signal

    elif strategy == "smart_money":
        candle_body = abs(latest_close - latest_open)
        candle_range = latest_high - latest_low if (latest_high - latest_low) != 0 else 1

        if (
            latest_close > latest_open
            and candle_body > candle_range * 0.6
            and latest_close > prev_close
            and structure == "Bullish Structure"
        ):
            final_signal = "BUY"
            confidence = max(confidence, 72)

        elif (
            latest_close < latest_open
            and candle_body > candle_range * 0.6
            and latest_close < prev_close
            and structure == "Bearish Structure"
        ):
            final_signal = "SELL"
            confidence = max(confidence, 72)

        else:
            final_signal = "HOLD"

    elif strategy == "ema_rsi":
        closes = df["close"]
        avg_close = closes.tail(15).mean()
        if latest_close > avg_close and prev_close < avg_close:
            final_signal = "BUY"
            confidence = max(confidence, 65)
        elif latest_close < avg_close and prev_close > avg_close:
            final_signal = "SELL"
            confidence = max(confidence, 65)
        else:
            final_signal = "HOLD"

    else:
        if raw_signal == "BUY" and structure == "Bullish Structure" and confidence >= bot_config["min_confidence"]:
            final_signal = "BUY"
        elif raw_signal == "SELL" and structure == "Bearish Structure" and confidence >= bot_config["min_confidence"]:
            final_signal = "SELL"
        else:
            final_signal = "HOLD"

    return {
        "signal": final_signal,
        "bias": get_bias_from_signal(final_signal) if final_signal != "HOLD" else bias,
        "structure": structure,
        "regime": regime,
        "confidence": confidence,
        "trade_idea": trade_idea
    }


def calculate_trade_levels(df, signal):
    latest_close = float(df.iloc[-1]["close"])
    latest_high = float(df.iloc[-1]["high"])
    latest_low = float(df.iloc[-1]["low"])

    if signal == "BUY":
        sl = latest_low * 0.995
        tp = latest_close + (latest_close - sl) * bot_config["risk_reward"]
        return {
            "entry": round(latest_close, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2)
        }

    if signal == "SELL":
        sl = latest_high * 1.005
        tp = latest_close - (sl - latest_close) * bot_config["risk_reward"]
        return {
            "entry": round(latest_close, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2)
        }

    return {
        "entry": round(latest_close, 2),
        "sl": round(latest_close, 2),
        "tp": round(latest_close, 2)
    }


def get_symbol_summary(symbol, strategy="bot"):
    df = fetch_binance(symbol)
    if df is None:
        return None

    price = float(df.iloc[-1]["close"])
    evaluation = evaluate_bot_window(df, strategy=strategy)

    return {
        "symbol": symbol,
        "price": round(price, 2),
        "signal": evaluation["signal"],
        "bias": evaluation["bias"],
        "structure": evaluation["structure"],
        "regime": evaluation["regime"],
        "confidence": evaluation["confidence"],
        "trade_idea": evaluation["trade_idea"]
    }


def get_engine_snapshot():
    for symbol in bot_config["symbols"]:
        summary = get_symbol_summary(symbol, strategy="bot")
        if summary:
            return summary

    return {
        "symbol": "BTCUSDT",
        "price": 0,
        "signal": "HOLD",
        "bias": "Neutral",
        "structure": "Range / Mixed",
        "regime": "Unknown",
        "confidence": 50,
        "trade_idea": "No live data"
    }


# ---------------- ACCOUNT ----------------
def get_balance():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT SUM(pnl) FROM trades WHERE status='CLOSED'")
    total_pnl = c.fetchone()[0] or 0

    conn.close()
    return 10000 + total_pnl


# ---------------- ALERTS ----------------
def add_alert(message):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
    INSERT INTO alerts VALUES (?, ?, ?)
    """, (
        str(uuid.uuid4()),
        message,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()


# ---------------- TRADES ----------------
def get_open_trades():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT * FROM trades WHERE status='OPEN'")
    rows = c.fetchall()

    conn.close()
    return rows


def open_trade(symbol, signal, price):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    risk_amount = get_balance() * (bot_config["risk_percent"] / 100)

    sl = price * 0.99 if signal == "BUY" else price * 1.01
    tp = (
        price + (price - sl) * bot_config["risk_reward"]
        if signal == "BUY"
        else price - (sl - price) * bot_config["risk_reward"]
    )

    stop_distance = abs(price - sl)
    size = risk_amount / stop_distance if stop_distance else 0

    trade_id = str(uuid.uuid4())

    c.execute("""
    INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'OPEN', ?)
    """, (
        trade_id,
        symbol,
        signal,
        price,
        sl,
        tp,
        size,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()

    add_alert(f"🚀 OPEN {symbol} {signal} @ {round(price, 2)}")


def close_trade(trade_id, exit_price, pnl, symbol):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
    UPDATE trades
    SET exit=?, pnl=?, status='CLOSED'
    WHERE id=?
    """, (exit_price, pnl, trade_id))

    conn.commit()
    conn.close()

    add_alert(f"✅ CLOSED {symbol} PnL: {round(pnl, 2)}")


def update_trades(symbol, price):
    open_trades = get_open_trades()

    for trade in open_trades:
        trade_id, sym, type_, entry, sl, tp, size, _, _, _, _ = trade

        if sym != symbol:
            continue

        pnl = (price - entry) * size if type_ == "BUY" else (entry - price) * size

        if (
            (type_ == "BUY" and (price <= sl or price >= tp))
            or
            (type_ == "SELL" and (price >= sl or price <= tp))
        ):
            close_trade(trade_id, price, pnl, sym)


# ---------------- CHART DATA ----------------
def get_chart_candles(symbol="BTCUSDT", interval="1m", limit=200):
    df = fetch_binance(symbol, interval=interval, limit=limit)
    if df is None:
        return []

    candles = []
    for _, row in df.iterrows():
        candles.append({
            "time": int(row["time"].timestamp()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"])
        })
    return candles


def get_chart_signals(symbol="BTCUSDT", interval="1m", limit=200):
    raw = fetch_binance_raw(symbol=symbol, interval=interval, limit=limit)
    df = raw_candles_to_df(raw)

    if df is None or len(df) < 30:
        return {
            "markers": [],
            "trade_levels": [],
            "annotations": []
        }

    markers = []
    trade_levels = []
    annotations = []

    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    times = [int(t.timestamp()) for t in df["time"]]

    for i in range(20, len(df)):
        window_df = df.iloc[:i + 1].copy().reset_index(drop=True)
        evaluation = evaluate_bot_window(window_df, strategy="bot")
        signal = evaluation["signal"]

        if signal == "BUY":
            levels = calculate_trade_levels(window_df, signal)
            markers.append({
                "time": times[i],
                "position": "belowBar",
                "color": "#22c55e",
                "shape": "arrowUp",
                "text": f"BUY {symbol}"
            })
            trade_levels.append({
                "time": times[i],
                "side": "BUY",
                "entry": levels["entry"],
                "sl": levels["sl"],
                "tp": levels["tp"]
            })

        elif signal == "SELL":
            levels = calculate_trade_levels(window_df, signal)
            markers.append({
                "time": times[i],
                "position": "aboveBar",
                "color": "#ef4444",
                "shape": "arrowDown",
                "text": f"SELL {symbol}"
            })
            trade_levels.append({
                "time": times[i],
                "side": "SELL",
                "entry": levels["entry"],
                "sl": levels["sl"],
                "tp": levels["tp"]
            })

    recent_high = max(highs[-30:])
    recent_low = min(lows[-30:])
    t1 = times[-30]
    t2 = times[-1]

    annotations.append({
        "type": "line",
        "label": "BOS High",
        "price": round(recent_high, 2),
        "color": "#3b82f6",
        "startTime": t1,
        "endTime": t2
    })

    annotations.append({
        "type": "line",
        "label": "Liquidity Low",
        "price": round(recent_low, 2),
        "color": "#f59e0b",
        "startTime": t1,
        "endTime": t2
    })

    ob_top = max(highs[-12:-8])
    ob_bottom = min(lows[-12:-8])

    annotations.append({
        "type": "rectangle",
        "label": "Order Block",
        "color": "rgba(34,197,94,0.18)",
        "borderColor": "rgba(34,197,94,0.7)",
        "startTime": times[-12],
        "endTime": times[-4],
        "top": round(ob_top, 2),
        "bottom": round(ob_bottom, 2)
    })

    fvg_top = max(highs[-8:-6])
    fvg_bottom = min(lows[-8:-6])

    annotations.append({
        "type": "rectangle",
        "label": "FVG",
        "color": "rgba(239,68,68,0.16)",
        "borderColor": "rgba(239,68,68,0.7)",
        "startTime": times[-8],
        "endTime": times[-2],
        "top": round(fvg_top, 2),
        "bottom": round(fvg_bottom, 2)
    })

    return {
        "markers": markers,
        "trade_levels": trade_levels[-8:],
        "annotations": annotations
    }


# ---------------- BACKTESTER ----------------
def generate_backtest_signals(candles, strategy="bot"):
    df = raw_candles_to_df(candles)
    signals = []

    if df is None or len(df) < 21:
        return signals

    for i in range(20, len(df)):
        window_df = df.iloc[:i + 1].copy().reset_index(drop=True)
        evaluation = evaluate_bot_window(window_df, strategy=strategy)
        signal_type = evaluation["signal"]

        if signal_type not in ["BUY", "SELL"]:
            continue

        levels = calculate_trade_levels(window_df, signal_type)
        signal_time = window_df.iloc[-1]["time"].strftime("%Y-%m-%d %H:%M:%S")

        signals.append({
            "index": i,
            "type": signal_type,
            "price": levels["entry"],
            "time": signal_time,
            "stop_loss": levels["sl"],
            "take_profit": levels["tp"],
            "confidence": evaluation["confidence"],
            "structure": evaluation["structure"],
            "regime": evaluation["regime"]
        })

    return signals


def run_backtest_engine(candles, signals, starting_balance=1000):
    balance = float(starting_balance)
    trades = []

    if not candles or not signals:
        summary = {
            "starting_balance": round(starting_balance, 2),
            "final_balance": round(balance, 2),
            "net_pnl": 0.0,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "best_trade": 0.0,
            "win_rate": 0.0
        }
        return summary, trades

    for signal in signals:
        entry_index = signal["index"]
        entry_price = float(signal["price"])
        side = signal["type"]
        stop_loss = float(signal["stop_loss"])
        take_profit = float(signal["take_profit"])
        entry_time = signal["time"]

        exit_price = entry_price
        exit_time = entry_time
        pnl = 0.0

        max_forward_index = min(entry_index + 30, len(candles) - 1)

        for j in range(entry_index + 1, max_forward_index + 1):
            candle = candles[j]
            high = float(candle[2])
            low = float(candle[3])
            close = float(candle[4])
            candle_time = datetime.utcfromtimestamp(candle[0] / 1000).strftime("%Y-%m-%d %H:%M:%S")

            if side == "BUY":
                if low <= stop_loss:
                    exit_price = stop_loss
                    exit_time = candle_time
                    pnl = stop_loss - entry_price
                    break
                elif high >= take_profit:
                    exit_price = take_profit
                    exit_time = candle_time
                    pnl = take_profit - entry_price
                    break

            elif side == "SELL":
                if high >= stop_loss:
                    exit_price = stop_loss
                    exit_time = candle_time
                    pnl = entry_price - stop_loss
                    break
                elif low <= take_profit:
                    exit_price = take_profit
                    exit_time = candle_time
                    pnl = entry_price - take_profit
                    break

            if j == max_forward_index:
                exit_price = close
                exit_time = candle_time
                pnl = (exit_price - entry_price) if side == "BUY" else (entry_price - exit_price)

        balance += pnl

        trades.append({
            "side": side,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(take_profit, 2),
            "entry_time": entry_time,
            "exit_time": exit_time,
            "pnl": round(pnl, 2)
        })

    total_trades = len(trades)
    wins = len([t for t in trades if t["pnl"] > 0])
    losses = len([t for t in trades if t["pnl"] <= 0])
    net_pnl = round(sum(t["pnl"] for t in trades), 2)
    best_trade = round(max([t["pnl"] for t in trades], default=0), 2)
    win_rate = round((wins / total_trades) * 100, 2) if total_trades > 0 else 0.0

    summary = {
        "starting_balance": round(starting_balance, 2),
        "final_balance": round(balance, 2),
        "net_pnl": net_pnl,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "best_trade": best_trade,
        "win_rate": win_rate
    }

    return summary, trades


# ---------------- API ROUTES ----------------
@app.route("/live_trades")
def live_trades():
    results = []
    open_trades = get_open_trades()

    for trade in open_trades:
        trade_id, symbol, type_, entry, sl, tp, size, _, _, _, time_opened = trade

        df = fetch_binance(symbol)
        if df is None:
            continue

        price = float(df.iloc[-1]["close"])
        pnl = (price - entry) * size if type_ == "BUY" else (entry - price) * size

        results.append({
            "id": trade_id,
            "symbol": symbol,
            "type": type_,
            "entry": round(entry, 2),
            "price": round(price, 2),
            "size": round(size, 4),
            "pnl": round(pnl, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "time": time_opened
        })

    return jsonify(results)


@app.route("/chart-confirmation")
def chart_confirmation():
    tab = request.args.get("tab", "commodities").lower()
    engine = get_engine_snapshot()

    if tab == "commodities":
        data = {
            "category": "Commodities",
            "bias": engine["bias"],
            "signal": engine["signal"],
            "regime": engine["regime"],
            "confidence": engine["confidence"],
            "fx": "Neutral to weak USD" if engine["signal"] == "BUY" else "USD strength watch",
            "commodities": engine["trade_idea"],
            "indices": "Moderate risk-on" if engine["signal"] == "BUY" else "Mixed / cautious"
        }

    elif tab == "currencies":
        data = {
            "category": "Currencies",
            "bias": "Neutral" if engine["signal"] == "HOLD" else engine["bias"],
            "signal": engine["signal"],
            "regime": engine["regime"],
            "confidence": max(45, min(85, engine["confidence"] - 8)),
            "fx": "Dollar decision zone" if engine["signal"] == "HOLD" else f"Directional bias from {engine['symbol']}",
            "commodities": "No major commodity conflict",
            "indices": "Waiting for broader alignment" if engine["signal"] == "HOLD" else "Macro support present"
        }

    elif tab == "indices":
        data = {
            "category": "Indices",
            "bias": engine["bias"],
            "signal": engine["signal"],
            "regime": "Risk-on" if engine["signal"] == "BUY" else ("Risk-off" if engine["signal"] == "SELL" else "Mixed"),
            "confidence": max(50, min(90, engine["confidence"])),
            "fx": "USD not blocking upside" if engine["signal"] == "BUY" else "Defensive dollar watch",
            "commodities": "Oil and metals supportive" if engine["signal"] == "BUY" else "Mixed commodity read",
            "indices": "Broad equity strength present" if engine["signal"] == "BUY" else ("Pressure on equities" if engine["signal"] == "SELL" else "No clean trend")
        }

    else:
        data = {
            "category": "Commodities",
            "bias": engine["bias"],
            "signal": engine["signal"],
            "regime": engine["regime"],
            "confidence": engine["confidence"],
            "fx": "Neutral context",
            "commodities": engine["trade_idea"],
            "indices": "Mixed"
        }

    return jsonify(data)


@app.route("/chart-status")
def chart_status():
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    strategy = request.args.get("strategy", "bot").lower()

    summary = get_symbol_summary(symbol, strategy=strategy)

    if not summary:
        return jsonify({
            "symbol": symbol,
            "price": 0,
            "signal": "HOLD",
            "bias": "Neutral",
            "structure": "Range / Mixed",
            "regime": "Unknown",
            "confidence": 50,
            "trade_idea": "No data available"
        })

    return jsonify(summary)


@app.route("/api/chart-candles")
def api_chart_candles():
    try:
        symbol = request.args.get("symbol", "BTCUSDT").upper()
        interval = request.args.get("interval", "1m")
        limit = int(request.args.get("limit", 200))

        candles = get_chart_candles(symbol=symbol, interval=interval, limit=limit)

        if not candles:
            return jsonify({
                "ok": False,
                "error": f"No candle data returned for {symbol} {interval}",
                "data": []
            }), 200

        return jsonify({
            "ok": True,
            "data": candles
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "data": []
        }), 500


@app.route("/api/chart-overlays")
def api_chart_overlays():
    try:
        symbol = request.args.get("symbol", "BTCUSDT").upper()
        interval = request.args.get("interval", "1m")
        limit = int(request.args.get("limit", 200))

        data = get_chart_signals(symbol=symbol, interval=interval, limit=limit)

        return jsonify({
            "ok": True,
            "data": data
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "data": {
                "markers": [],
                "trade_levels": [],
                "annotations": []
            }
        }), 500


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    try:
        data = request.get_json(force=True)

        symbol = str(data.get("symbol", "BTCUSDT")).upper()
        interval = str(data.get("interval", "5m"))
        limit = int(data.get("limit", 200))
        strategy = str(data.get("strategy", "bot")).lower()
        starting_balance = float(data.get("starting_balance", 1000))

        if limit < 50:
            limit = 50
        if limit > 1000:
            limit = 1000

        candles = fetch_binance_raw(symbol=symbol, interval=interval, limit=limit)
        signals = generate_backtest_signals(candles, strategy=strategy)
        summary, trades = run_backtest_engine(
            candles,
            signals,
            starting_balance=starting_balance
        )

        return jsonify({
            "summary": summary,
            "signals": signals,
            "trades": trades
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------- PAGE ROUTES ----------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/charts")
def charts():
    return render_template("charts.html")


@app.route("/analytics")
def analytics():
    return render_template("analytics.html")


@app.route("/realtime")
def realtime():
    return render_template("realtime.html")


@app.route("/backtester")
def backtester():
    return render_template("backtester.html")


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
