# ============================================================
# AI Trading Engine (SMC + EMA + RSI + ADVANCED BACKTESTER)
# ============================================================

from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import pandas as pd
import requests
import sqlite3
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ---------------- CONFIG ----------------
bot_config = {
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "risk_reward": 2,
    "risk_percent": 1,
    "min_confidence": 60
}

# ---------------- DATA ----------------
def fetch_binance(symbol, interval="1m", limit=500):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        data = requests.get(url, timeout=5).json()

        df = pd.DataFrame(data, columns=[
            "time","open","high","low","close","volume",
            "_","_","_","_","_","_"
        ])

        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)

        df["time"] = pd.to_datetime(df["time"], unit="ms")

        return df
    except:
        return None

# ---------------- MARKET LOGIC ----------------
def get_structure(df):
    sma20 = df["close"].tail(20).mean()
    if df["close"].iloc[-1] > sma20:
        return "Bullish Structure"
    elif df["close"].iloc[-1] < sma20:
        return "Bearish Structure"
    return "Range"

def get_market_regime(df):
    high = df["high"].tail(20).max()
    low = df["low"].tail(20).min()
    avg = df["close"].tail(20).mean()

    if avg == 0:
        return "Unknown"

    range_pct = (high - low) / avg * 100

    if range_pct > 2.5:
        return "Trending"
    elif range_pct > 1:
        return "Active"
    return "Range"

# ---------------- STRATEGY ----------------
def evaluate_bot_window(df):

    if df is None or len(df) < 30:
        return {"signal": "HOLD", "confidence": 50}

    closes = df["close"]

    latest = closes.iloc[-1]
    prev = closes.iloc[-2]

    # EMA
    ema_fast = closes.ewm(span=9).mean()
    ema_slow = closes.ewm(span=21).mean()

    # RSI
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    rsi_val = rsi.iloc[-1]

    # SMC
    body = abs(df.iloc[-1]["close"] - df.iloc[-1]["open"])
    rng = df.iloc[-1]["high"] - df.iloc[-1]["low"]

    structure = get_structure(df)
    regime = get_market_regime(df)

    signal = "HOLD"
    confidence = 50

    if structure == "Bullish Structure":
        if ema_fast.iloc[-1] > ema_slow.iloc[-1] and rsi_val > 55 and body > rng * 0.6:
            signal = "BUY"
            confidence = 80

    if structure == "Bearish Structure":
        if ema_fast.iloc[-1] < ema_slow.iloc[-1] and rsi_val < 45 and body > rng * 0.6:
            signal = "SELL"
            confidence = 80

    return {
        "signal": signal,
        "confidence": confidence,
        "structure": structure,
        "regime": regime
    }

# ---------------- BACKTEST ENGINE ----------------
def run_backtest(df, starting_balance=1000):

    balance = starting_balance
    trades = []
    open_trade = None

    for i in range(30, len(df)):

        candle = df.iloc[i]
        price = candle["close"]

        # ===== MANAGE TRADE =====
        if open_trade:
            high = candle["high"]
            low = candle["low"]

            exit_price = None

            if open_trade["side"] == "BUY":
                if low <= open_trade["sl"]:
                    exit_price = open_trade["sl"]
                elif high >= open_trade["tp"]:
                    exit_price = open_trade["tp"]

            if open_trade["side"] == "SELL":
                if high >= open_trade["sl"]:
                    exit_price = open_trade["sl"]
                elif low <= open_trade["tp"]:
                    exit_price = open_trade["tp"]

            if exit_price:
                pnl = (
                    (exit_price - open_trade["entry"]) * open_trade["size"]
                    if open_trade["side"] == "BUY"
                    else (open_trade["entry"] - exit_price) * open_trade["size"]
                )

                balance += pnl

                trades.append({
                    "side": open_trade["side"],
                    "entry": open_trade["entry"],
                    "exit": exit_price,
                    "pnl": round(pnl, 2)
                })

                open_trade = None

        # ===== OPEN TRADE =====
        if not open_trade:
            result = evaluate_bot_window(df.iloc[:i])

            if result["signal"] in ["BUY", "SELL"]:

                entry = price

                if result["signal"] == "BUY":
                    sl = entry * 0.99
                    tp = entry + (entry - sl) * 2
                else:
                    sl = entry * 1.01
                    tp = entry - (sl - entry) * 2

                risk = balance * 0.01
                size = risk / abs(entry - sl)

                open_trade = {
                    "side": result["signal"],
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "size": size
                }

    wins = len([t for t in trades if t["pnl"] > 0])
    total = len(trades)

    return {
        "balance": round(balance, 2),
        "trades": trades,
        "win_rate": round((wins / total * 100), 2) if total else 0
    }

# ---------------- API ----------------
@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    data = request.json

    symbol = data.get("symbol", "BTCUSDT")
    interval = data.get("interval", "5m")

    df = fetch_binance(symbol, interval)

    result = run_backtest(df)

    return jsonify(result)

@app.route("/api/chart-candles")
def candles():
    symbol = request.args.get("symbol", "BTCUSDT")
    df = fetch_binance(symbol)

    return jsonify([
        {
            "time": int(r["time"].timestamp()),
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"]
        } for _, r in df.iterrows()
    ])

# ---------------- PAGES ----------------
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

@app.route("/backtester")  # ✅ FIXED YOUR ERROR
def backtester():
    return render_template("backtester.html")

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
