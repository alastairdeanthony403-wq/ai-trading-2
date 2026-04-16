# ============================================================
# AI Trading Engine (REAL + DATABASE VERSION)
# ============================================================

from flask import Flask, render_template, request, jsonify
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

    conn.commit()
    conn.close()


def close_trade(trade_id, exit_price, pnl):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
    UPDATE trades
    SET exit=?, pnl=?, status='CLOSED'
    WHERE id=?
    """, (exit_price, pnl, trade_id))

    conn.commit()
    conn.close()


def get_balance():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT SUM(pnl) FROM trades WHERE status='CLOSED'")
    total_pnl = c.fetchone()[0] or 0

    conn.close()

    return 10000 + total_pnl


def update_trades(symbol, price):
    open_trades = get_open_trades()

    for t in open_trades:
        trade_id, sym, type_, entry, sl, tp, size, exit_p, pnl_db, status, time = t

        if sym != symbol:
            continue

        pnl = (price - entry) * size if type_ == "BUY" else (entry - price) * size

        if (type_ == "BUY" and (price <= sl or price >= tp)) or \
           (type_ == "SELL" and (price >= sl or price <= tp)):

            close_trade(trade_id, price, round(pnl, 2))


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

        # UPDATE TRADES
        update_trades(symbol, price)

        # OPEN NEW TRADE
        if sig in ["BUY", "SELL"] and symbol not in open_symbols:
            open_trade(symbol, sig, price)

        # GET CURRENT PNL
        current_trade = next((t for t in get_open_trades() if t[1] == symbol), None)

        pnl = 0
        if current_trade:
            _, _, type_, entry, _, _, size, _, _, _, _ = current_trade

            if type_ == "BUY":
                pnl = (price - entry) * size
            else:
                pnl = (entry - price) * size

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


# ---------------- ACCOUNT ----------------
@app.route("/account")
def account():
    return jsonify({
        "balance": round(get_balance(), 2)
    })


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
