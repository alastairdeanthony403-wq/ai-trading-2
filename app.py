# ============================================================
# AI Trading Engine (FULL PRO VERSION - REALTIME UPGRADE)
# ============================================================

from flask import Flask, render_template, jsonify
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
    "symbols": ["BTCUSDT", "ETHUSDT", "AAPL"],
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
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit=100"
        r = requests.get(url, timeout=5)

        if r.status_code != 200:
            return None

        data = r.json()

        if not isinstance(data, list):
            return None

        df = pd.DataFrame(data, columns=[
            "time","open","high","low","close","volume",
            "_","_","_","_","_","_"
        ])

        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)

        return df

    except:
        return None


# ---------------- SIGNAL ----------------
def generate_signal(df):
    price = df.iloc[-1]["close"]
    prev = df.iloc[-2]["close"]

    if price > prev:
        return "BUY"
    elif price < prev:
        return "SELL"
    return "HOLD"


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

    add_alert(f"✅ CLOSED {symbol} PnL: {round(pnl,2)}")

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


# ============================================================
# 🚀 NEW REALTIME ROUTE (THIS IS THE UPGRADE)
# ============================================================
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
            "tp": round(tp, 2)
        })

    return jsonify(results)


# ---------------- ROUTES ----------------
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
    app.run(host="0.0.0.0", port=10000)
