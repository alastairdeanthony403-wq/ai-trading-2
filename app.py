# ============================================================
# AI Trading Engine (UNIFIED BOT LOGIC + TRUE BACKTESTER)
# Upgraded: HTF bias + liquidity sweep + BOS + FVG retrace
# + anti-overtrading + backtester metrics
# + trade_analysis learning table
# ============================================================

from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import pandas as pd
import requests
import uuid
import sqlite3
import os
from datetime import datetime, timedelta, timezone


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

app = Flask(__name__, template_folder=TEMPLATE_DIR)
CORS(app)

# ---------------- CONFIG ----------------
bot_config = {
    "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"],
    "risk_reward": 2,
    "risk_percent": 1,
    "min_confidence": 75,
    "starting_balance": 10000,

    # ANTI-OVERTRADING RULES
    "max_trades_per_day": 5,
    "max_daily_loss_percent": 3,
    "max_consecutive_losses": 2,
    "avoid_quiet_market": True,
    "avoid_sideways_market": True,
    "min_volume_multiplier": 0.8,

    # STRATEGY RULES
    "min_smc_score": 7,
    "blocked_crypto_hours_utc": [0, 1, 2, 3],

    "paper_min_closed_trades": 100,
    "paper_min_profit_factor": 1.3,
    "paper_max_drawdown_percent": 10,
    "paper_max_consecutive_losses": 3,
    "paper_min_backtest_ranges": 2,
    "real_trading_enabled": False,
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
    "4h": 3600,
}


# ---------------- DATABASE ----------------
def get_conn():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = get_conn()
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

    c.execute("""
    CREATE TABLE IF NOT EXISTS trade_analysis (
        id TEXT PRIMARY KEY,
        symbol TEXT,
        timeframe TEXT,
        strategy TEXT,
        session TEXT,
        trend_bias TEXT,
        higher_tf_bias TEXT,
        liquidity_sweep TEXT,
        bos TEXT,
        structure TEXT,
        regime TEXT,
        confidence REAL,
        smc_score REAL,
        result TEXT,
        pnl REAL,
        reason_for_entry TEXT,
        reason_for_exit TEXT,
        entry_time TEXT,
        exit_time TEXT,
        created_at TEXT
    )
    """)
   
    c.execute("""
    CREATE TABLE IF NOT EXISTS backtest_runs (
        id TEXT PRIMARY KEY,
        symbol TEXT,
        interval TEXT,
        strategy TEXT,
        start_date TEXT,
        end_date TEXT,
        total_trades INTEGER,
        net_pnl REAL,
        profit_factor REAL,
        max_drawdown REAL,
        max_drawdown_percent REAL,
        win_rate REAL,
        created_at TEXT
    )
    """)
    conn.commit()
    conn.close()


init_db()

# ---------------- HELPERS ----------------
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _request_json(url, params=None, timeout=10):
    return requests.get(url, params=params, timeout=timeout)


def add_alert(message):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO alerts VALUES (?, ?, ?)",
        (str(uuid.uuid4()), message, now_str())
    )
    conn.commit()
    conn.close()

def calculate_closed_paper_stats():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    SELECT pnl, time
    FROM trades
    WHERE status='CLOSED'
    ORDER BY time ASC
    """)

    rows = c.fetchall()
    conn.close()

    pnls = [float(r[0] or 0) for r in rows]

    total_trades = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2)

    balance = bot_config["starting_balance"]
    peak = balance
    max_drawdown = 0

    consecutive_losses = 0
    max_consecutive_losses = 0

    for pnl in pnls:
        balance += pnl
        peak = max(peak, balance)
        max_drawdown = max(max_drawdown, peak - balance)

        if pnl < 0:
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
        else:
            consecutive_losses = 0

    max_drawdown_percent = round((max_drawdown / bot_config["starting_balance"]) * 100, 2)

    return {
        "total_closed_paper_trades": total_trades,
        "profit_factor": profit_factor,
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_percent": max_drawdown_percent,
        "max_consecutive_losses": max_consecutive_losses,
        "net_pnl": round(sum(pnls), 2)
    }


def save_backtest_run(symbol, interval, strategy, start_date, end_date, summary):
    starting_balance = float(summary.get("starting_balance", bot_config["starting_balance"]) or bot_config["starting_balance"])
    max_drawdown = float(summary.get("max_drawdown", 0) or 0)
    max_drawdown_percent = round((max_drawdown / starting_balance) * 100, 2) if starting_balance else 0

    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    INSERT INTO backtest_runs (
        id, symbol, interval, strategy, start_date, end_date,
        total_trades, net_pnl, profit_factor, max_drawdown,
        max_drawdown_percent, win_rate, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(uuid.uuid4()),
        symbol,
        interval,
        strategy,
        start_date or "",
        end_date or "",
        int(summary.get("total_trades", 0) or 0),
        float(summary.get("net_pnl", 0) or 0),
        float(summary.get("profit_factor", 0) or 0),
        max_drawdown,
        max_drawdown_percent,
        float(summary.get("win_rate", 0) or 0),
        now_str()
    ))

    conn.commit()
    conn.close()


def count_good_backtest_ranges():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    SELECT COUNT(*)
    FROM backtest_runs
    WHERE total_trades >= 10
    AND net_pnl > 0
    AND profit_factor >= ?
    AND max_drawdown_percent <= ?
    """, (
        bot_config["paper_min_profit_factor"],
        bot_config["paper_max_drawdown_percent"]
    ))

    count = c.fetchone()[0] or 0
    conn.close()

    return count


def get_real_trading_readiness():
    paper_stats = calculate_closed_paper_stats()
    good_ranges = count_good_backtest_ranges()

    checks = {
        "minimum_100_closed_paper_trades": paper_stats["total_closed_paper_trades"] >= bot_config["paper_min_closed_trades"],
        "profit_factor_above_1_3": paper_stats["profit_factor"] >= bot_config["paper_min_profit_factor"],
        "max_drawdown_below_10_percent": paper_stats["max_drawdown_percent"] <= bot_config["paper_max_drawdown_percent"],
        "no_uncontrolled_losing_streaks": paper_stats["max_consecutive_losses"] <= bot_config["paper_max_consecutive_losses"],
        "works_across_multiple_date_ranges": good_ranges >= bot_config["paper_min_backtest_ranges"],
    }

    ready = all(checks.values())

    return {
        "ready_for_real_money": ready,
        "real_trading_enabled": bot_config.get("real_trading_enabled", False),
        "allowed_to_trade_real_money": ready and bot_config.get("real_trading_enabled", False),
        "paper_stats": paper_stats,
        "good_backtest_ranges": good_ranges,
        "required_good_backtest_ranges": bot_config["paper_min_backtest_ranges"],
        "checks": checks,
        "message": (
            "Ready for real-money review."
            if ready
            else "Not ready for real money. Continue paper trading and testing."
        )
    }


def block_real_trading_if_not_ready():
    readiness = get_real_trading_readiness()

    if not readiness["allowed_to_trade_real_money"]:
        return False, readiness

    return True, readiness
    
def save_trade_analysis(trade, signal, reason_for_exit="Backtest exit"):
    result = "WIN" if float(trade.get("pnl", 0) or 0) > 0 else "LOSS"

    reason_for_entry = (
        f"Signal={signal.get('type')}, "
        f"Strategy={signal.get('strategy')}, "
        f"HTF Bias={signal.get('higher_tf_bias')}, "
        f"Sweep={signal.get('liquidity_sweep')}, "
        f"BOS={signal.get('bos')}, "
        f"Structure={signal.get('structure')}, "
        f"Regime={signal.get('regime')}, "
        f"Confidence={signal.get('confidence')}, "
        f"SMC Score={signal.get('smc_score')}"
    )

    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    INSERT INTO trade_analysis (
        id, symbol, timeframe, strategy, session,
        trend_bias, higher_tf_bias, liquidity_sweep, bos,
        structure, regime, confidence, smc_score,
        result, pnl, reason_for_entry, reason_for_exit,
        entry_time, exit_time, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(uuid.uuid4()),
        trade.get("symbol", "N/A"),
        trade.get("timeframe", "N/A"),
        signal.get("strategy", "bot"),
        trade.get("session", "N/A"),
        signal.get("bias", "N/A"),
        signal.get("higher_tf_bias", "N/A"),
        signal.get("liquidity_sweep", "N/A"),
        signal.get("bos", "N/A"),
        signal.get("structure", "N/A"),
        signal.get("regime", "N/A"),
        float(signal.get("confidence", 0) or 0),
        float(signal.get("smc_score", 0) or 0),
        result,
        float(trade.get("pnl", 0) or 0),
        reason_for_entry,
        reason_for_exit,
        trade.get("entry_time", ""),
        trade.get("exit_time", ""),
        now_str()
    ))

    conn.commit()
    conn.close()


def get_balance():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT SUM(pnl) FROM trades WHERE status='CLOSED'")
    total_pnl = c.fetchone()[0] or 0
    conn.close()
    return bot_config["starting_balance"] + total_pnl


def get_trade_history(limit=100):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT symbol, type, entry, exit, pnl, status, time
        FROM trades
        WHERE status='CLOSED'
        ORDER BY time DESC
        LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()

    return [
        {
            "symbol": row[0],
            "side": row[1],
            "type": row[1],
            "entry_price": row[2],
            "exit_price": row[3],
            "entry": row[2],
            "exit": row[3],
            "pnl": row[4],
            "status": row[5],
            "time": row[6],
            "closed_at": row[6]
        }
        for row in rows
    ]


# ---------------- MARKET DATA ----------------
def _fetch_binance_klines(symbol, interval="1m", limit=100):
    last_error = None

    for base_url in BINANCE_BASE_URLS:
        try:
            response = _request_json(
                f"{base_url}/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=8
            )

            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) >= 2:
                    return data

            body = (response.text or "").strip()
            last_error = f"{base_url} HTTP {response.status_code}: {body[:250]}"

        except requests.exceptions.RequestException as e:
            last_error = f"{base_url} request failed: {str(e)}"

    raise RuntimeError(last_error or "All Binance endpoints failed")


def _coinbase_fetch_candles(product_id, granularity, total_needed):
    all_rows = []
    end_time = datetime.now(timezone.utc)

    while len(all_rows) < total_needed:
        batch_size = min(300, total_needed - len(all_rows))
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
            raise RuntimeError(f"Coinbase HTTP {response.status_code}: {response.text[:250]}")

        rows = response.json()
        if not isinstance(rows, list) or not rows:
            break

        all_rows.extend(rows)
        earliest_ts = min(r[0] for r in rows)
        end_time = datetime.fromtimestamp(earliest_ts, tz=timezone.utc) - timedelta(seconds=granularity)

        if len(rows) < batch_size:
            break

    unique_rows = {int(r[0]): r for r in all_rows if isinstance(r, list) and len(r) >= 6}
    ordered = [unique_rows[k] for k in sorted(unique_rows.keys())]

    if not ordered:
        raise RuntimeError("Coinbase returned no usable candle data")

    return ordered[-total_needed:]


def _aggregate_coinbase_1h_to_4h(rows, limit):
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
        raise RuntimeError(f"No Coinbase mapping for {symbol}")

    if interval not in COINBASE_GRANULARITY_MAP:
        raise RuntimeError(f"No Coinbase interval mapping for {interval}")

    if interval == "4h":
        raw_1h = _coinbase_fetch_candles(product_id, 3600, max(limit * 4, 4))
        rows = _aggregate_coinbase_1h_to_4h(raw_1h, limit)
    else:
        rows = _coinbase_fetch_candles(product_id, COINBASE_GRANULARITY_MAP[interval], limit)

    return [
        [
            int(r[0]) * 1000,
            str(r[3]),
            str(r[2]),
            str(r[1]),
            str(r[4]),
            str(r[5]),
        ]
        for r in rows
    ]


def fetch_binance_raw(symbol="BTCUSDT", interval="5m", limit=500):
    if not symbol or not symbol.endswith("USDT"):
        raise ValueError("Invalid symbol")

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

    for col in ["open", "high", "low", "close", "volume"]:
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


# ---------------- BASE BOT LOGIC ----------------
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


# ---------------- ADVANCED STRATEGY FILTERS ----------------
def get_higher_timeframe(interval):
    if interval in ["1m", "5m", "15m"]:
        return "1h"
    if interval == "1h":
        return "4h"
    return "4h"


def get_trend_bias(df):
    if df is None or len(df) < 50:
        return "Neutral"

    closes = df["close"]
    ema_fast = closes.ewm(span=20, adjust=False).mean()
    ema_slow = closes.ewm(span=50, adjust=False).mean()

    if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
        return "Bullish"
    elif ema_fast.iloc[-1] < ema_slow.iloc[-1]:
        return "Bearish"

    return "Neutral"


def detect_liquidity_sweep(df):
    if df is None or len(df) < 25:
        return None

    current = df.iloc[-1]
    previous_high = df["high"].iloc[-21:-1].max()
    previous_low = df["low"].iloc[-21:-1].min()

    if current["high"] > previous_high and current["close"] < previous_high:
        return "SELL_SWEEP"

    if current["low"] < previous_low and current["close"] > previous_low:
        return "BUY_SWEEP"

    return None


def detect_break_of_structure(df):
    if df is None or len(df) < 30:
        return None

    recent_high = df["high"].iloc[-15:-1].max()
    recent_low = df["low"].iloc[-15:-1].min()
    close = df.iloc[-1]["close"]

    if close > recent_high:
        return "BULLISH_BOS"

    if close < recent_low:
        return "BEARISH_BOS"

    return None


def price_in_discount_zone(df):
    if df is None or len(df) < 30:
        return False

    recent_high = df["high"].tail(30).max()
    recent_low = df["low"].tail(30).min()
    close = df.iloc[-1]["close"]

    midpoint = (recent_high + recent_low) / 2
    return close <= midpoint


def price_in_premium_zone(df):
    if df is None or len(df) < 30:
        return False

    recent_high = df["high"].tail(30).max()
    recent_low = df["low"].tail(30).min()
    close = df.iloc[-1]["close"]

    midpoint = (recent_high + recent_low) / 2
    return close >= midpoint


def detect_fvg_retrace(df, direction):
    if df is None or len(df) < 10:
        return False

    candles = df.tail(8).reset_index(drop=True)
    current_close = candles.iloc[-1]["close"]

    for i in range(2, len(candles)):
        c1 = candles.iloc[i - 2]
        c3 = candles.iloc[i]

        if direction == "BUY":
            if c3["low"] > c1["high"]:
                fvg_low = c1["high"]
                fvg_high = c3["low"]

                if fvg_low <= current_close <= fvg_high:
                    return True

        if direction == "SELL":
            if c3["high"] < c1["low"]:
                fvg_low = c3["high"]
                fvg_high = c1["low"]

                if fvg_low <= current_close <= fvg_high:
                    return True

    return False


def session_allowed():
    hour = datetime.utcnow().hour
    return hour not in bot_config["blocked_crypto_hours_utc"]


def evaluate_bot_window(df, strategy="bot", symbol="BTCUSDT", interval="5m"):
    if df is None or len(df) < 50:
        return {
            "signal": "HOLD",
            "bias": "Neutral",
            "structure": "Range / Mixed",
            "regime": "Unknown",
            "confidence": 50,
            "trade_idea": "Not enough data",
            "higher_tf": get_higher_timeframe(interval),
            "higher_tf_bias": "Neutral",
            "liquidity_sweep": None,
            "bos": None,
            "smc_score": 0
        }

    raw_signal = generate_signal(df)
    structure = get_structure(df)
    regime = get_market_regime(df)
    confidence = estimate_confidence(df, raw_signal)

    higher_tf = get_higher_timeframe(interval)
    higher_df = fetch_binance(symbol, interval=higher_tf, limit=100)
    higher_tf_bias = get_trend_bias(higher_df)

    liquidity_sweep = detect_liquidity_sweep(df)
    bos = detect_break_of_structure(df)

    final_signal = "HOLD"
    trade_idea = "Wait for clearer confirmation"
    smc_score = 0

    if strategy == "basic":
        final_signal = raw_signal
        trade_idea = get_trade_idea(final_signal)

    elif strategy == "ema_rsi":
        closes = df["close"]
        ema_fast = closes.ewm(span=9, adjust=False).mean()
        ema_slow = closes.ewm(span=21, adjust=False).mean()

        if ema_fast.iloc[-1] > ema_slow.iloc[-1] and confidence >= 65:
            final_signal = "BUY"
            confidence = max(confidence, 70)
            trade_idea = "EMA momentum long"

        elif ema_fast.iloc[-1] < ema_slow.iloc[-1] and confidence >= 65:
            final_signal = "SELL"
            confidence = max(confidence, 70)
            trade_idea = "EMA momentum short"

    elif strategy in ["smart_money", "bot"]:
        buy_conditions = [
            higher_tf_bias == "Bullish",
            liquidity_sweep == "BUY_SWEEP",
            bos == "BULLISH_BOS",
            price_in_discount_zone(df),
            detect_fvg_retrace(df, "BUY"),
            confidence >= bot_config["min_confidence"],
            regime not in ["Range / Quiet", "Unknown"],
            structure != "Range / Mixed",
            session_allowed()
        ]

        sell_conditions = [
            higher_tf_bias == "Bearish",
            liquidity_sweep == "SELL_SWEEP",
            bos == "BEARISH_BOS",
            price_in_premium_zone(df),
            detect_fvg_retrace(df, "SELL"),
            confidence >= bot_config["min_confidence"],
            regime not in ["Range / Quiet", "Unknown"],
            structure != "Range / Mixed",
            session_allowed()
        ]

        buy_score = sum(1 for condition in buy_conditions if condition)
        sell_score = sum(1 for condition in sell_conditions if condition)

        if buy_score >= bot_config["min_smc_score"]:
            final_signal = "BUY"
            confidence = max(confidence, 80)
            trade_idea = "HTF bullish + liquidity sweep + BOS + retrace entry"
            smc_score = buy_score

        elif sell_score >= bot_config["min_smc_score"]:
            final_signal = "SELL"
            confidence = max(confidence, 80)
            trade_idea = "HTF bearish + liquidity sweep + BOS + retrace entry"
            smc_score = sell_score

        else:
            smc_score = max(buy_score, sell_score)

    return {
        "signal": final_signal,
        "bias": get_bias_from_signal(final_signal) if final_signal != "HOLD" else higher_tf_bias,
        "structure": structure,
        "regime": regime,
        "confidence": confidence,
        "trade_idea": trade_idea,
        "higher_tf": higher_tf,
        "higher_tf_bias": higher_tf_bias,
        "liquidity_sweep": liquidity_sweep,
        "bos": bos,
        "smc_score": smc_score
    }


def calculate_trade_levels(df, signal):
    latest_close = float(df.iloc[-1]["close"])
    latest_high = float(df.iloc[-1]["high"])
    latest_low = float(df.iloc[-1]["low"])

    if signal == "BUY":
        sl = latest_low * 0.995
        tp = latest_close + (latest_close - sl) * bot_config["risk_reward"]
    elif signal == "SELL":
        sl = latest_high * 1.005
        tp = latest_close - (sl - latest_close) * bot_config["risk_reward"]
    else:
        sl = latest_close
        tp = latest_close

    return {
        "entry": round(latest_close, 2),
        "sl": round(sl, 2),
        "tp": round(tp, 2)
    }


def get_symbol_summary(symbol, strategy="bot", interval="1m"):
    df = fetch_binance(symbol, interval=interval, limit=200)
    if df is None:
        return None

    evaluation = evaluate_bot_window(
        df,
        strategy=strategy,
        symbol=symbol,
        interval=interval
    )

    prev_close = float(df.iloc[-2]["close"]) if len(df) > 1 else float(df.iloc[-1]["close"])
    last_close = float(df.iloc[-1]["close"])
    change_pct = ((last_close - prev_close) / prev_close * 100) if prev_close else 0

    return {
        "symbol": symbol,
        "price": round(last_close, 2),
        "live_price": round(last_close, 2),
        "change_pct": round(change_pct, 4),
        "signal": evaluation["signal"],
        "bias": evaluation["bias"],
        "structure": evaluation["structure"],
        "regime": evaluation["regime"],
        "confidence": evaluation["confidence"],
        "trade_idea": evaluation["trade_idea"],
        "higher_tf": evaluation["higher_tf"],
        "higher_tf_bias": evaluation["higher_tf_bias"],
        "liquidity_sweep": evaluation["liquidity_sweep"],
        "bos": evaluation["bos"],
        "smc_score": evaluation["smc_score"]
    }


def get_engine_snapshot():
    for symbol in bot_config["symbols"]:
        summary = get_symbol_summary(symbol, strategy="bot", interval="1m")
        if summary:
            return summary

    return {
        "symbol": "BTCUSDT",
        "price": 0,
        "live_price": 0,
        "signal": "HOLD",
        "bias": "Neutral",
        "structure": "Range / Mixed",
        "regime": "Unknown",
        "confidence": 50,
        "trade_idea": "No live data"
    }


# ---------------- OVERTRADING PROTECTION ----------------
def get_open_trades():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE status='OPEN'")
    rows = c.fetchall()
    conn.close()
    return rows


def get_today_closed_trades(symbol=None):
    conn = get_conn()
    c = conn.cursor()

    if symbol:
        c.execute("""
        SELECT symbol, pnl, time
        FROM trades
        WHERE status='CLOSED'
        AND symbol=?
        AND DATE(time)=DATE('now')
        ORDER BY time DESC
        """, (symbol,))
    else:
        c.execute("""
        SELECT symbol, pnl, time
        FROM trades
        WHERE status='CLOSED'
        AND DATE(time)=DATE('now')
        ORDER BY time DESC
        """)

    rows = c.fetchall()
    conn.close()

    return [{"symbol": r[0], "pnl": float(r[1] or 0), "time": r[2]} for r in rows]


def get_consecutive_losses(symbol):
    trades = get_today_closed_trades(symbol=symbol)
    losses = 0

    for trade in trades:
        if trade["pnl"] < 0:
            losses += 1
        else:
            break

    return losses


def get_today_total_pnl():
    return sum(t["pnl"] for t in get_today_closed_trades())


def market_is_too_quiet(df):
    if df is None or len(df) < 30:
        return True

    recent_volume = df["volume"].tail(10).mean()
    average_volume = df["volume"].tail(30).mean()

    if average_volume <= 0:
        return True

    return (recent_volume / average_volume) < bot_config["min_volume_multiplier"]


def market_is_sideways(df):
    if df is None or len(df) < 30:
        return True

    recent_high = df["high"].tail(30).max()
    recent_low = df["low"].tail(30).min()
    avg_price = df["close"].tail(30).mean()

    if avg_price <= 0:
        return True

    range_percent = ((recent_high - recent_low) / avg_price) * 100
    return range_percent < 0.6


def can_open_new_trade(symbol, df, evaluation):
    open_trades = get_open_trades()

    if any(t[1] == symbol for t in open_trades):
        return False, "Already has open trade"

    if evaluation["confidence"] < bot_config["min_confidence"]:
        return False, f"Confidence too low: {evaluation['confidence']}%"

    if get_consecutive_losses(symbol) >= bot_config["max_consecutive_losses"]:
        return False, "Consecutive loss limit reached"

    today_trades = get_today_closed_trades()
    if len(today_trades) >= bot_config["max_trades_per_day"]:
        return False, "Max trades per day reached"

    balance = get_balance()
    daily_pnl = get_today_total_pnl()
    max_daily_loss = balance * (bot_config["max_daily_loss_percent"] / 100)

    if daily_pnl <= -max_daily_loss:
        return False, "Max daily loss reached"

    if bot_config["avoid_quiet_market"] and market_is_too_quiet(df):
        return False, "Market too quiet"

    if bot_config["avoid_sideways_market"] and market_is_sideways(df):
        return False, "Market sideways"

    if evaluation["regime"] in ["Range / Quiet", "Unknown"]:
        return False, f"Bad regime: {evaluation['regime']}"

    if evaluation["structure"] == "Range / Mixed":
        return False, "Structure is mixed/ranging"

    if evaluation.get("smc_score", 0) < bot_config["min_smc_score"]:
        return False, f"SMC confirmation too weak: {evaluation.get('smc_score', 0)}"

    return True, "Allowed"


# ---------------- TRADES ----------------
def open_trade(symbol, signal, price):
    df = fetch_binance(symbol, interval="1m", limit=200)
    if df is None:
        return {"ok": False, "reason": "No market data"}

    evaluation = evaluate_bot_window(
        df,
        strategy="bot",
        symbol=symbol,
        interval="1m"
    )

    allowed, reason = can_open_new_trade(symbol, df, evaluation)

    if not allowed:
        add_alert(f"SKIPPED {symbol}: {reason}")
        return {"ok": False, "reason": reason}

    conn = get_conn()
    c = conn.cursor()

    risk_amount = get_balance() * (bot_config["risk_percent"] / 100)
    levels = calculate_trade_levels(df, signal)

    sl = levels["sl"]
    tp = levels["tp"]
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
        now_str()
    ))

    conn.commit()
    conn.close()

    add_alert(
        f"OPEN {symbol} {signal} @ {round(price, 2)} | "
        f"Confidence {round(evaluation['confidence'], 1)}% | "
        f"SMC Score {evaluation.get('smc_score', 0)}"
    )

    return {"ok": True, "trade_id": trade_id}


def close_trade(trade_id, exit_price, pnl, symbol):
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    UPDATE trades
    SET exit=?, pnl=?, status='CLOSED', time=?
    WHERE id=?
    """, (exit_price, pnl, now_str(), trade_id))

    conn.commit()
    conn.close()

    add_alert(f"CLOSED {symbol} PnL: {round(pnl, 2)}")


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

    return [
        {
            "time": int(row["time"].timestamp()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"])
        }
        for _, row in df.iterrows()
    ]


def get_chart_signals(symbol="BTCUSDT", interval="1m", limit=200):
    raw = fetch_binance_raw(symbol=symbol, interval=interval, limit=limit)
    df = raw_candles_to_df(raw)

    if df is None or len(df) < 50:
        return {"markers": [], "trade_levels": [], "annotations": []}

    markers = []
    trade_levels = []

    times = [int(t.timestamp()) for t in df["time"]]
    highs = df["high"].tolist()
    lows = df["low"].tolist()

    for i in range(50, len(df)):
        window_df = df.iloc[:i + 1].copy().reset_index(drop=True)

        evaluation = evaluate_bot_window(
            window_df,
            strategy="bot",
            symbol=symbol,
            interval=interval
        )

        signal = evaluation["signal"]

        if signal in ["BUY", "SELL"]:
            levels = calculate_trade_levels(window_df, signal)

            markers.append({
                "time": times[i],
                "position": "belowBar" if signal == "BUY" else "aboveBar",
                "color": "#22c55e" if signal == "BUY" else "#ef4444",
                "shape": "arrowUp" if signal == "BUY" else "arrowDown",
                "text": f"{signal} {symbol} | SMC {evaluation.get('smc_score', 0)}"
            })

            trade_levels.append({
                "time": times[i],
                "side": signal,
                "entry": levels["entry"],
                "sl": levels["sl"],
                "tp": levels["tp"]
            })

    annotations = []

    if len(df) >= 30:
        annotations.append({
            "type": "line",
            "label": "BOS High",
            "price": round(max(highs[-30:]), 2),
            "color": "#3b82f6",
            "startTime": times[-30],
            "endTime": times[-1]
        })

        annotations.append({
            "type": "line",
            "label": "Liquidity Low",
            "price": round(min(lows[-30:]), 2),
            "color": "#f59e0b",
            "startTime": times[-30],
            "endTime": times[-1]
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

    if df is None or len(df) < 50:
        return signals

    for i in range(50, len(df)):
        window_df = df.iloc[:i + 1].copy().reset_index(drop=True)

        evaluation = evaluate_bot_window(
            window_df,
            strategy=strategy,
            symbol=symbol,
            interval=interval
        )

        signal_type = evaluation["signal"]

        if signal_type not in ["BUY", "SELL"]:
            continue

        levels = calculate_trade_levels(window_df, signal_type)
        signal_time = window_df.iloc[-1]["time"].strftime("%Y-%m-%d %H:%M:%S")

        signals.append({
            "index": i,
            "symbol": symbol,
            "interval": interval,
            "strategy": strategy,
            "type": signal_type,
            "price": levels["entry"],
            "time": signal_time,
            "stop_loss": levels["sl"],
            "take_profit": levels["tp"],
            "confidence": evaluation["confidence"],
            "bias": evaluation.get("bias"),
            "structure": evaluation["structure"],
            "regime": evaluation["regime"],
            "trade_idea": evaluation.get("trade_idea"),
            "higher_tf": evaluation.get("higher_tf"),
            "higher_tf_bias": evaluation.get("higher_tf_bias"),
            "liquidity_sweep": evaluation.get("liquidity_sweep"),
            "bos": evaluation.get("bos"),
            "smc_score": evaluation.get("smc_score", 0)
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
        reason_for_exit = "Timed exit"

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
                    gross_pnl = stop_loss - entry_price
                    exit_time = candle_time
                    reason_for_exit = "Stop loss hit"
                    break
                elif high >= take_profit:
                    exit_price = take_profit
                    gross_pnl = take_profit - entry_price
                    exit_time = candle_time
                    reason_for_exit = "Take profit hit"
                    break

            elif side == "SELL":
                if high >= stop_loss:
                    exit_price = stop_loss
                    gross_pnl = entry_price - stop_loss
                    exit_time = candle_time
                    reason_for_exit = "Stop loss hit"
                    break
                elif low <= take_profit:
                    exit_price = take_profit
                    gross_pnl = entry_price - take_profit
                    exit_time = candle_time
                    reason_for_exit = "Take profit hit"
                    break

            if j == max_forward_index:
                exit_price = close
                gross_pnl = (exit_price - entry_price) if side == "BUY" else (entry_price - exit_price)
                exit_time = candle_time
                reason_for_exit = "Timed exit after max forward candles"

        fee_cost = abs(entry_price) * (fee_percent / 100)
        slippage_cost = abs(entry_price) * (slippage_percent / 100)
        net_pnl = gross_pnl - fee_cost - slippage_cost

        total_fees += fee_cost
        total_slippage += slippage_cost

        balance += net_pnl
        peak_balance = max(peak_balance, balance)
        max_drawdown = max(max_drawdown, peak_balance - balance)

        if net_pnl < 0:
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
        else:
            consecutive_losses = 0

        entry_dt = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
        session = get_session_name(entry_dt)
        session_performance[session] += net_pnl

        trade_record = {
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
        }

        trades.append(trade_record)

        save_trade_analysis(
            trade_record,
            signal,
            reason_for_exit=reason_for_exit
        )

    total_trades = len(trades)
    wins_list = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses_list = [t["pnl"] for t in trades if t["pnl"] < 0]

    wins = len(wins_list)
    losses = len(losses_list)

    gross_profit = sum(wins_list)
    gross_loss = abs(sum(losses_list))

    win_rate = round((wins / total_trades) * 100, 2) if total_trades else 0.0
    loss_rate = 100 - win_rate if total_trades else 0.0

    average_win = round(sum(wins_list) / wins, 2) if wins else 0.0
    average_loss = round(sum(losses_list) / losses, 2) if losses else 0.0
    expectancy = round(((win_rate / 100) * average_win) + ((loss_rate / 100) * average_loss), 2) if total_trades else 0.0
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else round(gross_profit, 2)

    net_pnl = round(sum(t["pnl"] for t in trades), 2)
    best_trade = round(max([t["pnl"] for t in trades], default=0), 2)
    worst_trade = round(min([t["pnl"] for t in trades], default=0), 2)

    if total_trades >= 2:
        first_date = datetime.strptime(trades[0]["entry_time"], "%Y-%m-%d %H:%M:%S")
        last_date = datetime.strptime(trades[-1]["entry_time"], "%Y-%m-%d %H:%M:%S")
        days = max((last_date - first_date).days, 1)
    else:
        days = 1

    symbol_perf = {}
    timeframe_perf = {}

    for trade in trades:
        symbol_perf[trade["symbol"]] = symbol_perf.get(trade["symbol"], 0) + trade["pnl"]
        timeframe_perf[trade["timeframe"]] = timeframe_perf.get(trade["timeframe"], 0) + trade["pnl"]

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
        "trades_per_day": round(total_trades / days, 2),
        "best_symbol": max(symbol_perf, key=symbol_perf.get) if symbol_perf else "N/A",
        "worst_symbol": min(symbol_perf, key=symbol_perf.get) if symbol_perf else "N/A",
        "best_timeframe": max(timeframe_perf, key=timeframe_perf.get) if timeframe_perf else "N/A",
        "worst_timeframe": min(timeframe_perf, key=timeframe_perf.get) if timeframe_perf else "N/A",
        "session_performance": {
            "London": round(session_performance["London"], 2),
            "New York": round(session_performance["New York"], 2),
            "Asia": round(session_performance["Asia"], 2)
        }
    }

    return summary, trades


# ---------------- API ROUTES ----------------
@app.route("/refresh-engine")
def refresh_engine():
    all_signals = []

    for symbol in bot_config["symbols"]:
        summary = get_symbol_summary(symbol, strategy="bot", interval="1m")
        if summary:
            all_signals.append(summary)

            df = fetch_binance(symbol, interval="1m", limit=200)
            if df is not None:
                price = float(df.iloc[-1]["close"])
                update_trades(symbol, price)

    return jsonify({
        "ok": True,
        "balance": round(get_balance(), 2),
        "all_signals": all_signals,
        "open_trades": live_trades().get_json(),
        "last_update": now_str()
    })


@app.route("/signal")
def signal_dashboard():
    all_signals = []

    for symbol in bot_config["symbols"]:
        summary = get_symbol_summary(symbol, strategy="bot", interval="1m")
        if summary:
            all_signals.append(summary)

    return jsonify({
        "balance": round(get_balance(), 2),
        "history": get_trade_history(limit=50),
        "all_signals": all_signals,
        "last_update": now_str()
    })


@app.route("/signals")
def signals_map():
    signal_data = {}

    for symbol in bot_config["symbols"]:
        summary = get_symbol_summary(symbol, strategy="bot", interval="1m")
        if summary:
            signal_data[symbol] = summary

    return jsonify(signal_data)


@app.route("/alerts")
def alerts():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT message, time FROM alerts ORDER BY time DESC LIMIT 20")
    rows = c.fetchall()
    conn.close()

    return jsonify([
        {
            "message": row[0],
            "time": row[1],
            "type": "info"
        }
        for row in rows
    ])


@app.route("/history")
def history():
    return jsonify(get_trade_history(limit=100))


@app.route("/stats")
def stats():
    history_rows = get_trade_history(limit=1000)

    total = len(history_rows)
    wins = len([t for t in history_rows if float(t.get("pnl") or 0) > 0])
    losses = len([t for t in history_rows if float(t.get("pnl") or 0) < 0])
    net_pnl = sum(float(t.get("pnl") or 0) for t in history_rows)
    win_rate = (wins / total * 100) if total else 0

    return jsonify({
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 2),
        "net_pnl": round(net_pnl, 2)
    })


@app.route("/equity")
def equity():
    history_rows = list(reversed(get_trade_history(limit=1000)))

    balance = bot_config["starting_balance"]
    points = [{"time": "Start", "equity": round(balance, 2)}]

    for trade in history_rows:
        balance += float(trade.get("pnl") or 0)
        points.append({
            "time": trade.get("closed_at") or trade.get("time"),
            "equity": round(balance, 2)
        })

    return jsonify(points)


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
        update_trades(symbol, price)

        pnl = (price - entry) * size if type_ == "BUY" else (entry - price) * size

        results.append({
            "id": trade_id,
            "symbol": symbol,
            "side": type_,
            "type": type_,
            "entry_price": round(entry, 2),
            "current_price": round(price, 2),
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

    return jsonify({
        "category": tab.title(),
        "bias": engine["bias"],
        "signal": engine["signal"],
        "regime": engine["regime"],
        "confidence": engine["confidence"],
        "fx": "Neutral context",
        "commodities": engine["trade_idea"],
        "indices": "Mixed"
    })


@app.route("/chart-status")
def chart_status():
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    strategy = request.args.get("strategy", "bot").lower()
    interval = request.args.get("interval", "1m")

    summary = get_symbol_summary(
        symbol,
        strategy=strategy,
        interval=interval
    )

    if not summary:
        return jsonify({
            "symbol": symbol,
            "price": 0,
            "live_price": 0,
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

        return jsonify({
            "ok": bool(candles),
            "data": candles,
            "error": None if candles else f"No candle data returned for {symbol} {interval}"
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


@app.route("/api/chart-overlays")
def api_chart_overlays():
    try:
        symbol = request.args.get("symbol", "BTCUSDT").upper()
        interval = request.args.get("interval", "1m")
        limit = int(request.args.get("limit", 200))

        data = get_chart_signals(symbol=symbol, interval=interval, limit=limit)

        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "data": {"markers": [], "trade_levels": [], "annotations": []}
        }), 500


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    try:
        data = request.get_json(force=True) or {}

        symbol = str(data.get("symbol", "BTCUSDT")).upper()
        interval = str(data.get("interval", "5m"))
        limit = int(data.get("limit", 200))
        strategy = str(data.get("strategy", "bot")).lower()
        starting_balance = float(data.get("starting_balance", 1000))
        fee_percent = float(data.get("fee_percent", 0.04))
        slippage_percent = float(data.get("slippage_percent", 0.02))
        start_date = data.get("start_date")
        end_date = data.get("end_date")

        limit = max(100, min(limit, 1000))

        candles = fetch_binance_raw(
            symbol=symbol,
            interval=interval,
            limit=limit
        )

        if not candles or len(candles) < 50:
            return jsonify({
                "error": "Not enough candle data found. Try increasing candles."
            }), 400

        signals = generate_backtest_signals(
            candles,
            symbol=symbol,
            interval=interval,
            strategy=strategy
        )

        summary, trades = run_backtest_engine(
            candles,
            signals,
            starting_balance=starting_balance,
            fee_percent=fee_percent,
            slippage_percent=slippage_percent
        )

        try:
            save_backtest_run(
                symbol=symbol,
                interval=interval,
                strategy=strategy,
                start_date=start_date,
                end_date=end_date,
                summary=summary
            )
        except Exception as save_error:
            print("Backtest saved skipped/error:", save_error)

        return jsonify({
            "ok": True,
            "summary": summary,
            "signals": signals,
            "trades": trades
        })
import traceback
print("BACKTEST ERROR:\n", traceback.format_exc())
    except Exception as e:
        print("BACKTEST ERROR:", str(e))
        return jsonify({"error": str(e)}), 500


# ---------------- PAGE ROUTES ----------------
def render_main_page():
    return render_template("preview.html")


@app.route("/")
def home():
    return render_main_page()


@app.route("/charts")
def charts():
    return render_main_page()


@app.route("/analytics")
def analytics():
    return render_main_page()


@app.route("/realtime")
def realtime():
    return render_main_page()


@app.route("/backtester")
def backtester():
    return render_main_page()

@app.route("/api/real-trading-readiness")
def api_real_trading_readiness():
    return jsonify(get_real_trading_readiness())


# ---------------- RUN ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
