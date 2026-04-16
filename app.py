# ============================================================
# AI Trading Engine (FULL PRO VERSION)
# Includes:
# - Database persistence
# - Equity curve
# - Stats (win rate, total trades)
# - Alerts system
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
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m&limit=100"
    data = requests.get(url).json()

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume",
        "_","_","_","_","_","_"
    ])

    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)

    return df

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

# ---------------- TRADE ENGINE ----------------
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

# ---------------- ROUTES ----------------

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/signal")
def signal():

    results = []

    open_trades = get_open_trades()
    open_symbols = [t[1] for t in open_trades]

    for symbol in bot_config["symbols"]:
        df = fetch_binance(symbol)
        price = df.iloc[-1]["close"]

        sig = generate_signal(df)

        update_trades(symbol, price)

        if sig in ["BUY", "SELL"] and symbol not in open_symbols:
            open_trade(symbol, sig, price)

        current_trade = next((t for t in get_open_trades() if t[1] == symbol), None)

        pnl = 0
        if current_trade:
            _, _, type_, entry, _, _, size, _, _, _, _ = current_trade
            pnl = (price - entry) * size if type_ == "BUY" else (entry - price) * size

        results.append({
            "symbol": symbol,
            "signal": sig,
            "price": round(price, 2),
            "live_price": round(price, 2),
            "pnl": round(pnl, 2),
            "confidence": 70
        })

    best = results[0] if results else None

    return jsonify({
        "best_trade": best,
        "all_signals": results
    })

# ---------------- HISTORY ----------------
@app.route("/history")
def history():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT symbol, type, entry, exit, pnl, time FROM trades WHERE status='CLOSED'")
    rows = c.fetchall()

    conn.close()

    return jsonify([
        {
            "symbol": r[0],
            "signal": r[1],
            "entry": r[2],
            "exit": r[3],
            "pnl": r[4],
            "time": r[5]
        }
        for r in rows
    ])

# ---------------- EQUITY CURVE ----------------
@app.route("/equity")
def equity():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT pnl, time FROM trades WHERE status='CLOSED' ORDER BY time")
    rows = c.fetchall()

    conn.close()

    balance = 10000
    curve = []

    for pnl, time in rows:
        balance += pnl
        curve.append({"time": time, "balance": round(balance, 2)})

    return jsonify(curve)

# ---------------- STATS ----------------
@app.route("/stats")
def stats():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT pnl FROM trades WHERE status='CLOSED'")
    pnls = [r[0] for r in c.fetchall()]

    conn.close()

    total = len(pnls)
    wins = len([p for p in pnls if p > 0])
    win_rate = (wins / total * 100) if total else 0

    return jsonify({
        "trades": total,
        "wins": wins,
        "win_rate": round(win_rate, 2)
    })

# ---------------- ALERTS ----------------
@app.route("/alerts")
def alerts():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT message, time FROM alerts ORDER BY time DESC LIMIT 20")
    rows = c.fetchall()

    conn.close()

    return jsonify([
        {"message": r[0], "time": r[1]}
        for r in rows
    ])

# ---------------- ACCOUNT ----------------
@app.route("/account")
def account():
    return jsonify({
        "balance": round(get_balance(), 2)
    })

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
