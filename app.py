# ============================================================
# AI Trading Engine (UNIFIED BOT LOGIC + TRUE BACKTESTER)
# DEPLOYMENT-SAFE VERSION
# ============================================================

import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

app = Flask(__name__, template_folder="templates")
CORS(app)

# ---------------- CONFIG ----------------
bot_config = {
    "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"],
    "risk_reward": 2,
    "risk_percent": 1,
    "min_confidence": 60,
    "starting_balance": 10000
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
    "4h": 3600,  # fetched as 1h and aggregated to 4h
}

optimizer_cache = {}
runtime_cache = {
    "signals": {},
    "last_prices": {},
    "last_update": None,
}

# ---------------- DATABASE ----------------
def get_conn():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id TEXT PRIMARY KEY,
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
        id TEXT PRIMARY KEY,
        message TEXT,
        type TEXT DEFAULT 'info',
        time TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS paper_open_trades (
        id TEXT PRIMARY KEY,
        symbol TEXT,
        side TEXT,
        entry_price REAL,
        current_price REAL,
        stop_loss REAL,
        take_profit REAL,
        size REAL,
        pnl REAL,
        confidence REAL,
        strategy TEXT,
        opened_at TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS paper_trade_history (
        id TEXT PRIMARY KEY,
        symbol TEXT,
        side TEXT,
        entry_price REAL,
        exit_price REAL,
        stop_loss REAL,
        take_profit REAL,
        size REAL,
        pnl REAL,
        confidence REAL,
        strategy TEXT,
        opened_at TEXT,
        closed_at TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS paper_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    c.execute("""
    INSERT OR IGNORE INTO paper_settings (key, value)
    VALUES ('paper_balance', ?)
    """, (str(bot_config["starting_balance"]),))

    conn.commit()
    conn.close()


init_db()

# ---------------- LOW LEVEL HELPERS ----------------
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _request_json(url, params=None, timeout=10):
    return requests.get(url, params=params, timeout=timeout)


def add_alert(message, alert_type="info"):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    INSERT INTO alerts (id, message, type, time)
    VALUES (?, ?, ?, ?)
    """, (str(uuid.uuid4()), message, alert_type, now_str()))
    conn.commit()
    conn.close()


def get_recent_alerts(limit=20):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    SELECT message, type, time
    FROM alerts
    ORDER BY time DESC
    LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()

    return [
        {"message": r[0], "type": r[1], "time": r[2]}
        for r in rows
    ]


def get_paper_balance():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM paper_settings WHERE key='paper_balance'")
    row = c.fetchone()
    conn.close()
    return float(row[0]) if row else float(bot_config["starting_balance"])


def set_paper_balance(value):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    INSERT INTO paper_settings (key, value)
    VALUES ('paper_balance', ?)
    ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (str(value),))
    conn.commit()
    conn.close()

# ---------------- MARKET DATA ----------------
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
                str(row[3]),
                str(row[2]),
                str(row[1]),
                str(row[4]),
                str(row[5]),
            ])
        return converted

    granularity = COINBASE_GRANULARITY_MAP[interval]
    rows = _coinbase_fetch_candles(product_id, granularity, limit)

    converted = []
    for row in rows:
        converted.append([
            int(row[0]) * 1000,
            str(row[3]),
            str(row[2]),
            str(row[1]),
            str(row[4]),
            str(row[5]),
        ])

    return converted


def fetch_market_raw(symbol="BTCUSDT", interval="5m", limit=500):
    if not symbol or not symbol.endswith("USDT"):
        raise ValueError("Invalid symbol")

    if limit < 1:
        raise ValueError("Invalid candle limit")

    primary_error = None

    try:
        return _fetch_binance_klines(symbol, interval=interval, limit=limit)
    except Exception as e:
        primary_error = str(e)

    try:
        return _fetch_coinbase_raw(symbol=symbol, interval=interval, limit=limit)
    except Exception as fallback_error:
        raise RuntimeError(
            f"Primary source failed ({primary_error}) | Fallback source failed ({fallback_error})"
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


def fetch_market_df(symbol="BTCUSDT", interval="1m", limit=100):
    try:
        raw = fetch_market_raw(symbol=symbol, interval=interval, limit=limit)
        return raw_candles_to_df(raw)
    except Exception:
        return None

# ---------------- OPTIMIZATION ----------------
def optimize_strategy(df, interval="1m"):
    if df is None or len(df) < 80:
        return {
            "ema_fast": 9,
            "ema_slow": 21,
            "rsi_buy": 55,
            "rsi_sell": 45,
            "score": 50
        }

    cache_key = f"{interval}_{len(df)}_{round(float(df.iloc[-1]['close']), 4)}"
    if cache_key in optimizer_cache:
        return optimizer_cache[cache_key]

    closes = pd.to_numeric(df["close"], errors="coerce")
    if closes.isna().all():
        result = {
            "ema_fast": 9,
            "ema_slow": 21,
            "rsi_buy": 55,
            "rsi_sell": 45,
            "score": 50
        }
        optimizer_cache[cache_key] = result
        return result

    recent_vol = closes.pct_change().rolling(20).std().iloc[-1]
    if pd.isna(recent_vol):
        recent_vol = 0.01

    if recent_vol > 0.02:
        result = {
            "ema_fast": 7,
            "ema_slow": 18,
            "rsi_buy": 58,
            "rsi_sell": 42,
            "score": 72
        }
    elif recent_vol > 0.01:
        result = {
            "ema_fast": 9,
            "ema_slow": 21,
            "rsi_buy": 55,
            "rsi_sell": 45,
            "score": 64
        }
    else:
        result = {
            "ema_fast": 12,
            "ema_slow": 26,
            "rsi_buy": 52,
            "rsi_sell": 48,
            "score": 57
        }

    optimizer_cache[cache_key] = result
    return result


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

# ---------------- CORE BOT LOGIC ----------------
def generate_signal(df):
    if df is None or len(df) < 2:
        return "HOLD"

    latest = float(df.iloc[-1]["close"])
    previous = float(df.iloc[-2]["close"])

    if latest > previous:
        return "BUY"
    elif latest < previous:
        return "SELL"
    return "HOLD"


def get_structure(df):
    if df is None or len(df) < 20:
        return "Range / Mixed"

    closes = pd.to_numeric(df["close"], errors="coerce")
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

    recent_high = pd.to_numeric(df["high"], errors="coerce").tail(20).max()
    recent_low = pd.to_numeric(df["low"], errors="coerce").tail(20).min()
    avg_price = pd.to_numeric(df["close"], errors="coerce").tail(20).mean()

    if avg_price == 0 or pd.isna(avg_price):
        return "Unknown"

    range_percent = ((recent_high - recent_low) / avg_price) * 100

    if range_percent > 2.5:
        return "Trending"
    elif range_percent > 1.0:
        return "Active"
    return "Range / Quiet"


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


def evaluate_bot_window(df, symbol="BTCUSDT", interval="1m", strategy="bot"):
    if df is None or len(df) < 30:
        return {
            "signal": "HOLD",
            "bias": "Neutral",
            "structure": "Range / Mixed",
            "regime": "Unknown",
            "confidence": 50,
            "trade_idea": "Not enough data"
        }

    df = df.copy().reset_index(drop=True)

    opt = optimize_strategy(df.tail(200), interval=interval)

    closes = pd.to_numeric(df["close"], errors="coerce")
    opens = pd.to_numeric(df["open"], errors="coerce")
    highs = pd.to_numeric(df["high"], errors="coerce")
    lows = pd.to_numeric(df["low"], errors="coerce")

    ema_fast = closes.ewm(span=opt["ema_fast"], adjust=False).mean()
    ema_slow = closes.ewm(span=opt["ema_slow"], adjust=False).mean()
    rsi = calculate_rsi(closes, period=14)

    latest_close = float(closes.iloc[-1])
    latest_open = float(opens.iloc[-1])
    latest_high = float(highs.iloc[-1])
    latest_low = float(lows.iloc[-1])
    prev_close = float(closes.iloc[-2])
    rsi_val = float(rsi.iloc[-1])

    structure = get_structure(df)
    regime = get_market_regime(df)

    body = abs(latest_close - latest_open)
    rng = max(latest_high - latest_low, 1e-9)

    confidence = 50
    if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
        confidence += 15
    else:
        confidence -= 15

    if latest_close > prev_close:
        confidence += 10
    else:
        confidence -= 10

    if rsi_val > 55:
        confidence += 10
    elif rsi_val < 45:
        confidence -= 10

    if structure == "Bullish Structure":
        confidence += 10
    elif structure == "Bearish Structure":
        confidence -= 10

    confidence = max(5, min(95, confidence))

    signal = "HOLD"

    if strategy == "basic":
        if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
            signal = "BUY"
        elif ema_fast.iloc[-1] < ema_slow.iloc[-1]:
            signal = "SELL"

    elif strategy == "smart_money":
        if body > rng * 0.6 and structure == "Bullish Structure" and confidence >= 60:
            signal = "BUY"
        elif body > rng * 0.6 and structure == "Bearish Structure" and confidence <= 40:
            signal = "SELL"

    elif strategy == "ema_rsi":
        if ema_fast.iloc[-1] > ema_slow.iloc[-1] and rsi_val >= opt["rsi_buy"]:
            signal = "BUY"
        elif ema_fast.iloc[-1] < ema_slow.iloc[-1] and rsi_val <= opt["rsi_sell"]:
            signal = "SELL"

    else:
        if confidence >= 70 and structure == "Bullish Structure":
            signal = "BUY"
        elif confidence <= 30 and structure == "Bearish Structure":
            signal = "SELL"

    return {
        "signal": signal,
        "bias": get_bias_from_signal(signal if signal != "HOLD" else generate_signal(df)),
        "structure": structure,
        "regime": regime,
        "confidence": confidence,
        "trade_idea": get_trade_idea(signal)
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


def get_symbol_summary(symbol, strategy="bot", interval="1m"):
    df = fetch_market_df(symbol=symbol, interval=interval, limit=200)
    if df is None:
        return None

    price = float(df.iloc[-1]["close"])
    evaluation = evaluate_bot_window(df, symbol=symbol, interval=interval, strategy=strategy)

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
        summary = get_symbol_summary(symbol=symbol, strategy="bot", interval="1m")
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

# ---------------- PAPER TRADING PERSISTENCE ----------------
def get_paper_open_trades():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    SELECT id, symbol, side, entry_price, current_price, stop_loss, take_profit,
           size, pnl, confidence, strategy, opened_at
    FROM paper_open_trades
    ORDER BY opened_at DESC
    """)
    rows = c.fetchall()
    conn.close()

    return [
        {
            "id": r[0],
            "symbol": r[1],
            "side": r[2],
            "entry_price": float(r[3]),
            "current_price": float(r[4]),
            "stop_loss": float(r[5]),
            "take_profit": float(r[6]),
            "size": float(r[7]),
            "pnl": float(r[8]),
            "confidence": float(r[9]),
            "strategy": r[10],
            "opened_at": r[11],
        }
        for r in rows
    ]


def save_paper_open_trade(trade):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    INSERT OR REPLACE INTO paper_open_trades (
        id, symbol, side, entry_price, current_price, stop_loss, take_profit,
        size, pnl, confidence, strategy, opened_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade["id"],
        trade["symbol"],
        trade["side"],
        trade["entry_price"],
        trade["current_price"],
        trade["stop_loss"],
        trade["take_profit"],
        trade["size"],
        trade["pnl"],
        trade["confidence"],
        trade["strategy"],
        trade["opened_at"],
    ))
    conn.commit()
    conn.close()


def delete_paper_open_trade(trade_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM paper_open_trades WHERE id=?", (trade_id,))
    conn.commit()
    conn.close()


def save_paper_trade_history(trade):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    INSERT INTO paper_trade_history (
        id, symbol, side, entry_price, exit_price, stop_loss, take_profit,
        size, pnl, confidence, strategy, opened_at, closed_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade["id"],
        trade["symbol"],
        trade["side"],
        trade["entry_price"],
        trade["exit_price"],
        trade["stop_loss"],
        trade["take_profit"],
        trade["size"],
        trade["pnl"],
        trade["confidence"],
        trade["strategy"],
        trade["opened_at"],
        trade["closed_at"],
    ))
    conn.commit()
    conn.close()


def get_trade_history(limit=200):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    SELECT symbol, side, entry_price, exit_price, pnl, closed_at
    FROM paper_trade_history
    ORDER BY closed_at DESC
    LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()

    return [
        {
            "symbol": r[0],
            "side": r[1],
            "entry_price": float(r[2]),
            "exit_price": float(r[3]),
            "pnl": float(r[4]),
            "time": r[5]
        }
        for r in rows
    ]

# ---------------- LIVE ENGINE ----------------
def maybe_open_trade(symbol, interval="1m", strategy="bot"):
    open_trades = get_paper_open_trades()
    if any(t["symbol"] == symbol for t in open_trades):
        return

    df = fetch_market_df(symbol=symbol, interval=interval, limit=200)
    if df is None or len(df) < 30:
        return

    evaluation = evaluate_bot_window(df, symbol=symbol, interval=interval, strategy=strategy)
    signal = evaluation["signal"]

    if signal not in ["BUY", "SELL"]:
        return

    levels = calculate_trade_levels(df, signal)
    balance = get_paper_balance()
    risk_amount = balance * (bot_config["risk_percent"] / 100.0)
    stop_distance = abs(levels["entry"] - levels["sl"])
    size = risk_amount / stop_distance if stop_distance > 0 else 0

    trade = {
        "id": str(uuid.uuid4()),
        "symbol": symbol,
        "side": signal,
        "entry_price": float(levels["entry"]),
        "current_price": float(levels["entry"]),
        "stop_loss": float(levels["sl"]),
        "take_profit": float(levels["tp"]),
        "size": float(size),
        "pnl": 0.0,
        "confidence": float(evaluation["confidence"]),
        "strategy": strategy,
        "opened_at": now_str(),
    }

    save_paper_open_trade(trade)
    add_alert(f"OPEN {symbol} {signal} @ {levels['entry']}", "buy" if signal == "BUY" else "sell")


def update_live_trades():
    open_trades = get_paper_open_trades()
    if not open_trades:
        return

    for trade in open_trades:
        symbol = trade["symbol"]
        df = fetch_market_df(symbol=symbol, interval="1m", limit=5)
        if df is None:
            continue

        current_price = float(df.iloc[-1]["close"])
        runtime_cache["last_prices"][symbol] = current_price

        pnl = (
            (current_price - trade["entry_price"]) * trade["size"]
            if trade["side"] == "BUY"
            else (trade["entry_price"] - current_price) * trade["size"]
        )

        trade["current_price"] = current_price
        trade["pnl"] = pnl
        save_paper_open_trade(trade)

        should_close = False
        exit_price = current_price

        if trade["side"] == "BUY":
            if current_price <= trade["stop_loss"]:
                should_close = True
                exit_price = trade["stop_loss"]
            elif current_price >= trade["take_profit"]:
                should_close = True
                exit_price = trade["take_profit"]
        else:
            if current_price >= trade["stop_loss"]:
                should_close = True
                exit_price = trade["stop_loss"]
            elif current_price <= trade["take_profit"]:
                should_close = True
                exit_price = trade["take_profit"]

        if should_close:
            closed_pnl = (
                (exit_price - trade["entry_price"]) * trade["size"]
                if trade["side"] == "BUY"
                else (trade["entry_price"] - exit_price) * trade["size"]
            )

            history_trade = {
                "id": trade["id"],
                "symbol": trade["symbol"],
                "side": trade["side"],
                "entry_price": trade["entry_price"],
                "exit_price": exit_price,
                "stop_loss": trade["stop_loss"],
                "take_profit": trade["take_profit"],
                "size": trade["size"],
                "pnl": closed_pnl,
                "confidence": trade["confidence"],
                "strategy": trade["strategy"],
                "opened_at": trade["opened_at"],
                "closed_at": now_str(),
            }

            save_paper_trade_history(history_trade)
            delete_paper_open_trade(trade["id"])
            set_paper_balance(get_paper_balance() + closed_pnl)
            add_alert(
                f"CLOSED {trade['symbol']} {trade['side']} PnL: {round(closed_pnl, 2)}",
                "info"
            )


def refresh_engine():
    all_signals = []
    for symbol in bot_config["symbols"]:
        summary = get_symbol_summary(symbol=symbol, strategy="bot", interval="1m")
        if summary:
            runtime_cache["signals"][symbol] = summary
            runtime_cache["last_prices"][symbol] = summary["price"]
            all_signals.append({
                "symbol": summary["symbol"],
                "signal": summary["signal"],
                "live_price": summary["price"],
                "confidence": summary["confidence"],
                "bias": summary["bias"],
                "structure": summary["structure"],
                "regime": summary["regime"],
                "trade_idea": summary["trade_idea"]
            })

    update_live_trades()

    for symbol in bot_config["symbols"]:
        maybe_open_trade(symbol, interval="1m", strategy="bot")

    runtime_cache["last_update"] = now_str()
    return all_signals

# ---------------- CHART DATA ----------------
def get_chart_candles(symbol="BTCUSDT", interval="1m", limit=200):
    df = fetch_market_df(symbol=symbol, interval=interval, limit=limit)
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
    raw = fetch_market_raw(symbol=symbol, interval=interval, limit=limit)
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

    times = [int(t.timestamp()) for t in df["time"]]
    highs = df["high"].tolist()
    lows = df["low"].tolist()

    for i in range(30, len(df)):
        window_df = df.iloc[:i + 1].copy().reset_index(drop=True)
        evaluation = evaluate_bot_window(window_df, symbol=symbol, interval=interval, strategy="bot")
        signal = evaluation["signal"]

        if signal in ["BUY", "SELL"]:
            levels = calculate_trade_levels(window_df.iloc[:-1], signal)

            markers.append({
                "time": times[i],
                "position": "belowBar" if signal == "BUY" else "aboveBar",
                "color": "#22c55e" if signal == "BUY" else "#ef4444",
                "shape": "arrowUp" if signal == "BUY" else "arrowDown",
                "text": f"{signal} {symbol}"
            })

            trade_levels.append({
                "time": times[i],
                "side": signal,
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
def generate_backtest_signals(candles, symbol="BTCUSDT", interval="5m", strategy="bot"):
    df = raw_candles_to_df(candles)
    signals = []

    if df is None or len(df) < 31:
        return signals

    for i in range(30, len(df) - 1):
        signal_window = df.iloc[:i + 1].copy().reset_index(drop=True)
        evaluation = evaluate_bot_window(signal_window, symbol=symbol, interval=interval, strategy=strategy)
        signal_type = evaluation["signal"]

        if signal_type not in ["BUY", "SELL"]:
            continue

        planned_levels = calculate_trade_levels(signal_window, signal_type)
        next_candle = df.iloc[i + 1]
        entry_price = float(next_candle["open"])
        signal_time = next_candle["time"].strftime("%Y-%m-%d %H:%M:%S")

        if signal_type == "BUY":
            stop_loss = float(planned_levels["sl"])
            take_profit = round(entry_price + (entry_price - stop_loss) * bot_config["risk_reward"], 2)
        else:
            stop_loss = float(planned_levels["sl"])
            take_profit = round(entry_price - (stop_loss - entry_price) * bot_config["risk_reward"], 2)

        signals.append({
            "index": i + 1,
            "symbol": symbol,
            "interval": interval,
            "type": signal_type,
            "price": round(entry_price, 2),
            "time": signal_time,
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(take_profit, 2),
            "confidence": evaluation["confidence"],
            "structure": evaluation["structure"],
            "regime": evaluation["regime"]
        })

    return signals


def get_session_name(dt):
    hour = dt.hour

    if 7 <= hour < 12:
        return "London"
    elif 12 <= hour < 21:
        return "New York"
    return "Asia"


def run_backtest_engine(candles, signals, starting_balance=1000, fee_percent=0.04, slippage_percent=0.02):
    balance = float(starting_balance)
    peak_balance = balance
    max_drawdown = 0.0
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
            "worst_trade": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "expectancy": 0.0,
            "max_consecutive_losses": 0,
            "fees_paid": 0.0,
            "slippage_paid": 0.0,
            "trades_per_day": 0.0,
            "best_symbol": "N/A",
            "worst_symbol": "N/A",
            "best_timeframe": "N/A",
            "worst_timeframe": "N/A",
            "session_performance": {
                "London": 0.0,
                "New York": 0.0,
                "Asia": 0.0
            }
        }
        return summary, trades

    total_fees = 0.0
    total_slippage = 0.0
    consecutive_losses = 0
    max_consecutive_losses = 0
    session_performance = {
        "London": 0.0,
        "New York": 0.0,
        "Asia": 0.0
    }

    for signal in signals:
        entry_index = signal["index"]
        entry_price = float(signal["price"])
        side = signal["type"]
        stop_loss = float(signal["stop_loss"])
        take_profit = float(signal["take_profit"])
        entry_time = signal["time"]

        symbol = signal.get("symbol", "N/A")
        timeframe = signal.get("interval", "N/A")

        exit_price = entry_price
        exit_time = entry_time
        gross_pnl = 0.0

        max_forward_index = min(entry_index + 30, len(candles) - 1)

        for j in range(entry_index, max_forward_index + 1):
            candle = candles[j]
            high = float(candle[2])
            low = float(candle[3])
            close = float(candle[4])
            candle_time = datetime.utcfromtimestamp(candle[0] / 1000).strftime("%Y-%m-%d %H:%M:%S")

            if side == "BUY":
                if low <= stop_loss:
                    exit_price = stop_loss
                    exit_time = candle_time
                    gross_pnl = stop_loss - entry_price
                    break
                elif high >= take_profit:
                    exit_price = take_profit
                    exit_time = candle_time
                    gross_pnl = take_profit - entry_price
                    break

            elif side == "SELL":
                if high >= stop_loss:
                    exit_price = stop_loss
                    exit_time = candle_time
                    gross_pnl = entry_price - stop_loss
                    break
                elif low <= take_profit:
                    exit_price = take_profit
                    exit_time = candle_time
                    gross_pnl = entry_price - take_profit
                    break

            if j == max_forward_index:
                exit_price = close
                exit_time = candle_time
                gross_pnl = (exit_price - entry_price) if side == "BUY" else (entry_price - exit_price)

        fee_cost = abs(entry_price) * (fee_percent / 100)
        slippage_cost = abs(entry_price) * (slippage_percent / 100)
        net_pnl = gross_pnl - fee_cost - slippage_cost

        total_fees += fee_cost
        total_slippage += slippage_cost

        balance += net_pnl
        peak_balance = max(peak_balance, balance)

        drawdown = peak_balance - balance
        max_drawdown = max(max_drawdown, drawdown)

        if net_pnl < 0:
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
        else:
            consecutive_losses = 0

        entry_dt = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
        session = get_session_name(entry_dt)
        session_performance[session] += net_pnl

        trades.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "session": session,
            "side": side,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(take_profit, 2),
            "entry_time": entry_time,
            "exit_time": exit_time,
            "gross_pnl": round(gross_pnl, 2),
            "fee_cost": round(fee_cost, 2),
            "slippage_cost": round(slippage_cost, 2),
            "pnl": round(net_pnl, 2)
        })

    total_trades = len(trades)
    wins_list = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses_list = [t["pnl"] for t in trades if t["pnl"] < 0]

    wins = len(wins_list)
    losses = len(losses_list)

    gross_profit = sum(wins_list)
    gross_loss = abs(sum(losses_list))

    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else round(gross_profit, 2)

    net_pnl = round(sum(t["pnl"] for t in trades), 2)
    best_trade = round(max([t["pnl"] for t in trades], default=0), 2)
    worst_trade = round(min([t["pnl"] for t in trades], default=0), 2)
    average_win = round(sum(wins_list) / wins, 2) if wins else 0.0
    average_loss = round(sum(losses_list) / losses, 2) if losses else 0.0
    win_rate = round((wins / total_trades) * 100, 2) if total_trades else 0.0
    loss_rate = 100 - win_rate if total_trades else 0.0

    expectancy = round(
        ((win_rate / 100) * average_win) + ((loss_rate / 100) * average_loss),
        2
    ) if total_trades else 0.0

    first_date = datetime.strptime(trades[0]["entry_time"], "%Y-%m-%d %H:%M:%S")
    last_date = datetime.strptime(trades[-1]["entry_time"], "%Y-%m-%d %H:%M:%S")
    days = max((last_date - first_date).days, 1)
    trades_per_day = round(total_trades / days, 2)

    symbol_perf = {}
    timeframe_perf = {}

    for trade in trades:
        symbol_perf[trade["symbol"]] = symbol_perf.get(trade["symbol"], 0) + trade["pnl"]
        timeframe_perf[trade["timeframe"]] = timeframe_perf.get(trade["timeframe"], 0) + trade["pnl"]

    best_symbol = max(symbol_perf, key=symbol_perf.get) if symbol_perf else "N/A"
    worst_symbol = min(symbol_perf, key=symbol_perf.get) if symbol_perf else "N/A"
    best_timeframe = max(timeframe_perf, key=timeframe_perf.get) if timeframe_perf else "N/A"
    worst_timeframe = min(timeframe_perf, key=timeframe_perf.get) if timeframe_perf else "N/A"

    summary = {
        "starting_balance": round(starting_balance, 2),
        "final_balance": round(balance, 2),
        "net_pnl": net_pnl,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown": round(max_drawdown, 2),
        "average_win": average_win,
        "average_loss": average_loss,
        "expectancy": expectancy,
        "max_consecutive_losses": max_consecutive_losses,
        "fees_paid": round(total_fees, 2),
        "slippage_paid": round(total_slippage, 2),
        "trades_per_day": trades_per_day,
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "best_timeframe": best_timeframe,
        "worst_timeframe": worst_timeframe,
        "session_performance": {
            "London": round(session_performance["London"], 2),
            "New York": round(session_performance["New York"], 2),
            "Asia": round(session_performance["Asia"], 2)
        }
    }

    return summary, trades

# ---------------- ANALYTICS ----------------
def get_stats_payload():
    history = get_trade_history(limit=1000)
    total_trades = len(history)
    wins = len([t for t in history if t["pnl"] > 0])
    losses = len([t for t in history if t["pnl"] < 0])
    net_pnl = round(sum(t["pnl"] for t in history), 2)
    win_rate = round((wins / total_trades) * 100, 2) if total_trades > 0 else 0.0

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "net_pnl": net_pnl,
        "win_rate": win_rate
    }


def get_equity_payload():
    history = list(reversed(get_trade_history(limit=1000)))
    equity = bot_config["starting_balance"]
    points = []

    for trade in history:
        equity += trade["pnl"]
        points.append({
            "time": trade["time"],
            "equity": round(equity, 2)
        })

    return points

# ---------------- API ROUTES ----------------
@app.route("/signal")
def signal():
    history = get_trade_history(limit=20)

    all_signals = []
    for symbol in bot_config["symbols"]:
        cached = runtime_cache["signals"].get(symbol)
        if cached:
            all_signals.append({
                "symbol": cached["symbol"],
                "signal": cached["signal"],
                "live_price": cached["price"],
                "confidence": cached["confidence"],
                "bias": cached["bias"],
                "structure": cached["structure"],
                "regime": cached["regime"],
                "trade_idea": cached["trade_idea"]
            })

    return jsonify({
        "balance": get_paper_balance(),
        "all_signals": all_signals,
        "history": history,
        "last_update": runtime_cache.get("last_update")
    })


@app.route("/refresh-engine")
def refresh_engine_route():
    try:
        all_signals = refresh_engine()
        return jsonify({
            "ok": True,
            "signals": all_signals,
            "last_update": runtime_cache.get("last_update")
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route("/signals")
def signals():
    all_signals = refresh_engine()
    payload = {}
    for item in all_signals:
        payload[item["symbol"]] = {
            "signal": item["signal"],
            "confidence": item["confidence"],
            "change_pct": 0.0
        }
    return jsonify(payload)


@app.route("/live_trades")
def live_trades():
    refresh_engine()
    return jsonify(get_paper_open_trades())


@app.route("/alerts")
def alerts():
    return jsonify(get_recent_alerts(limit=20))


@app.route("/history")
def history():
    return jsonify(get_trade_history(limit=100))


@app.route("/stats")
def stats():
    return jsonify(get_stats_payload())


@app.route("/equity")
def equity():
    return jsonify(get_equity_payload())


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
    interval = request.args.get("interval", "1m")

    summary = get_symbol_summary(symbol, strategy=strategy, interval=interval)

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
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    interval = request.args.get("interval", "1m")
    limit = int(request.args.get("limit", 200))

    candles = get_chart_candles(symbol=symbol, interval=interval, limit=limit)
    return jsonify({"ok": True, "data": candles})


@app.route("/api/chart-overlays")
def api_chart_overlays():
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    interval = request.args.get("interval", "1m")
    limit = int(request.args.get("limit", 200))

    data = get_chart_signals(symbol=symbol, interval=interval, limit=limit)
    return jsonify({"ok": True, "data": data})


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    try:
        data = request.get_json(force=True)

        symbol = str(data.get("symbol", "BTCUSDT")).upper()
        interval = str(data.get("interval", "5m"))
        limit = int(data.get("limit", 200))
        strategy = str(data.get("strategy", "bot")).lower()
        starting_balance = float(data.get("starting_balance", 1000))

        start_date = data.get("start_date")
        end_date = data.get("end_date")

        if limit < 50:
            limit = 50
        if limit > 1000:
            limit = 1000

        candles = fetch_market_raw(symbol=symbol, interval=interval, limit=limit)

        # FILTER CANDLES BY DATE RANGE
        if start_date or end_date:
            filtered = []

            start_dt = None
            end_dt = None

            if start_date:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

            if end_date:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
                    hour=23,
                    minute=59,
                    second=59,
                    tzinfo=timezone.utc
                )

            for candle in candles:
                candle_dt = datetime.fromtimestamp(int(candle[0]) / 1000, tz=timezone.utc)

                if start_dt and candle_dt < start_dt:
                    continue

                if end_dt and candle_dt > end_dt:
                    continue

                filtered.append(candle)

            candles = filtered

        if not candles or len(candles) < 50:
            return jsonify({
                "error": "Not enough candle data found for that date range. Try a wider range or more candles."
            }), 400

        signals = generate_backtest_signals(
            candles,
            symbol=symbol,
            interval=interval,
            strategy=strategy
        )

        fee_percent = float(data.get("fee_percent", 0.04))
slippage_percent = float(data.get("slippage_percent", 0.02))

        summary, trades = run_backtest_engine(
            candles,
            signals,
            starting_balance=starting_balance,
            fee_percent=fee_percent,
            slippage_percent=slippage_percent
        )

        return jsonify({
            "summary": summary,
            "signals": signals,
            "trades": trades,
            "date_range": {
                "start_date": start_date,
                "end_date": end_date
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------- PAGE ROUTES ----------------
@app.route("/")
def home():
    return render_template("preview.html")


@app.route("/charts")
def charts():
    return render_template("preview.html")


@app.route("/analytics")
def analytics():
    return render_template("preview.html")


@app.route("/realtime")
def realtime():
    return render_template("preview.html")


@app.route("/backtester")
def backtester():
    return render_template("preview.html")

# ---------------- RUN ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
