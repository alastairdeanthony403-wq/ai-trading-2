# ============================================================
# AI Trading Engine (FULL PRO VERSION - REALTIME + CHARTS API)
# ============================================================

from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import pandas as pd
import requests
import uuid
import sqlite3
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ---------------- CONFIG ----------------
bot_config = {
    "symbols": ["BTCUSDT", "ETHUSDT"],   # Removed AAPL because Binance won't support it
    "risk_reward": 2,
    "risk_percent": 1
}

DB_NAME = "trades.db"

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

# ---------------- DATA ----------------
def fetch_binance(symbol):
    """
    Fetch 1-minute Binance candles.
    Returns pandas DataFrame or None if symbol unsupported/fetch fails.
    """
    try:
        if not symbol.endswith("USDT"):
            return None

        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit=100"
        r = requests.get(url, timeout=5)

        if r.status_code != 200:
            return None

        data = r.json()

        if not isinstance(data, list) or len(data) < 2:
            return None

        df = pd.DataFrame(data, columns=[
            "time", "open", "high", "low", "close", "volume",
            "_1", "_2", "_3", "_4", "_5", "_6"
        ])

        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df.dropna(inplace=True)

        if len(df) < 2:
            return None

        return df

    except Exception:
        return None

# ---------------- SIGNAL ----------------
def generate_signal(df):
    if df is None or len(df) < 2:
        return "HOLD"

    price = df.iloc[-1]["close"]
    prev = df.iloc[-2]["close"]

    if price > prev:
        return "BUY"
    elif price < prev:
        return "SELL"
    return "HOLD"

# ---------------- STRUCTURE / CONTEXT ----------------
def get_structure(df):
    """
    Simple structure read:
    - Bullish if latest close > rolling mean and recent closes rising
    - Bearish if latest close < rolling mean and recent closes falling
    - Else range
    """
    if df is None or len(df) < 20:
        return "Range / Mixed"

    close = df["close"]
    sma20 = close.tail(20).mean()

    latest = close.iloc[-1]
    prev1 = close.iloc[-2]
    prev2 = close.iloc[-3]

    if latest > sma20 and latest > prev1 > prev2:
        return "Bullish Structure"
    elif latest < sma20 and latest < prev1 < prev2:
        return "Bearish Structure"
    else:
        return "Range / Mixed"

def get_market_regime(df):
    if df is None or len(df) < 20:
        return "Unknown"

    high_low_range = (df["high"].tail(20).max() - df["low"].tail(20).min())
    avg_price = df["close"].tail(20).mean()

    if avg_price == 0:
        return "Unknown"

    range_percent = (high_low_range / avg_price) * 100

    if range_percent > 2.5:
        return "Trending"
    elif range_percent > 1.0:
        return "Active"
    else:
        return "Range / Quiet"

def estimate_confidence(df, signal):
    """
    Very simple confidence estimate based on:
    - price vs SMA20
    - direction consistency
    """
    if df is None or len(df) < 20:
        return 50

    close = df["close"]
    sma20 = close.tail(20).mean()
    latest = close.iloc[-1]
    prev = close.iloc[-2]

    confidence = 50

    if signal == "BUY":
        if latest > sma20:
            confidence += 15
        if latest > prev:
            confidence += 10
        if latest > close.tail(5).mean():
            confidence += 10

    elif signal == "SELL":
        if latest < sma20:
            confidence += 15
        if latest < prev:
            confidence += 10
        if latest < close.tail(5).mean():
            confidence += 10

    return max(35, min(95, confidence))

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
    """, (str(uuid.uuid4()), message, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

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
    tp = price + (price - sl) * bot_config["risk_reward"] if signal == "BUY" else price - (sl - price) * bot_config["risk_reward"]

    stop_distance = abs(price - sl)
    size = risk_amount / stop_distance if stop_distance else 0

    trade_id = str(uuid.uuid4())

    c.execute("""
    INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'OPEN', ?)
    """, (
        trade_id, symbol, signal, price, sl, tp, size,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    add_alert(f"🚀 OPEN {symbol} {signal} @ {price}")

    conn.commit()
    conn.close()

def close_trade(trade_id, exit_price, pnl, symbol):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
    UPDATE trades
    SET exit=?, pnl=?, status='CLOSED'
    WHERE id=?
    """, (exit_price, pnl, trade_id))

    add_alert(f"✅ CLOSED {symbol} PnL: {round(pnl, 2)}")

    conn.commit()
    conn.close()

def update_trades(symbol, price):
    open_trades = get_open_trades()

    for t in open_trades:
        trade_id, sym, type_, entry, sl, tp, size, _, _, _, _ = t

        if sym != symbol:
            continue

        pnl = (price - entry) * size if type_ == "BUY" else (entry - price) * size

        if (type_ == "BUY" and (price <= sl or price >= tp)) or \
           (type_ == "SELL" and (price >= sl or price <= tp)):
            close_trade(trade_id, price, pnl, sym)

# ---------------- ENGINE SUMMARY HELPERS ----------------
def get_symbol_summary(symbol):
    df = fetch_binance(symbol)
    if df is None:
        return None

    price = float(df.iloc[-1]["close"])
    signal = generate_signal(df)
    structure = get_structure(df)
    regime = get_market_regime(df)
    confidence = estimate_confidence(df, signal)

    if signal == "BUY":
        bias = "Bullish"
        trade_idea = "Pullback long / continuation"
    elif signal == "SELL":
        bias = "Bearish"
        trade_idea = "Reject highs / continuation short"
    else:
        bias = "Neutral"
        trade_idea = "Wait for clearer confirmation"

    return {
        "symbol": symbol,
        "price": round(price, 2),
        "signal": signal,
        "bias": bias,
        "structure": structure,
        "regime": regime,
        "confidence": confidence,
        "trade_idea": trade_idea
    }

def get_engine_snapshot():
    """
    Use first valid configured symbol as engine reference.
    """
    for symbol in bot_config["symbols"]:
        summary = get_symbol_summary(symbol)
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

# ---------------- REALTIME ROUTE ----------------
@app.route("/live_trades")
def live_trades():
    results = []
    open_trades = get_open_trades()

    for t in open_trades:
        trade_id, symbol, type_, entry, sl, tp, size, _, _, _, time = t

        df = fetch_binance(symbol)
        if df is None:
            continue

        price = df.iloc[-1]["close"]
        pnl = (price - entry) * size if type_ == "BUY" else (entry - price) * size

        results.append({
            "symbol": symbol,
            "type": type_,
            "entry": round(entry, 2),
            "price": round(price, 2),
            "size": round(size, 4),
            "pnl": round(pnl, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "time": time
        })

    return jsonify(results)

# ============================================================
# NEW CHARTS API ROUTES
# ============================================================

@app.route("/chart-confirmation")
def chart_confirmation():
    """
    Returns dynamic sidebar data for the /charts page.
    Frontend can call:
    /chart-confirmation?tab=commodities
    /chart-confirmation?tab=currencies
    /chart-confirmation?tab=indices
    """
    tab = request.args.get("tab", "commodities").lower()

    engine = get_engine_snapshot()

    if tab == "commodities":
        data = {
            "category": "Commodities",
            "bias": engine["bias"],
            "signal": engine["signal"],
            "regime": engine["regime"],
            "confidence": engine["confidence"],
            "fx": "Neutral to weak USD" if engine["bias"] == "Bullish" else "USD strength watch",
            "commodities": engine["trade_idea"],
            "indices": "Moderate risk-on" if engine["signal"] == "BUY" else "Mixed / cautious"
        }

    elif tab == "currencies":
        # Slightly softer framing for FX tab
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
    """
    Lightweight chart data endpoint.
    Frontend can call:
    /chart-status?symbol=BTCUSDT
    """
    symbol = request.args.get("symbol", "BTCUSDT").upper()

    summary = get_symbol_summary(symbol)

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

# ---------------- OPTIONAL EXISTING STATS ROUTES PLACEHOLDERS ----------------
# Keep/add your own history/equity/stats/alerts routes here if already built

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

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
