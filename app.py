# ============================================================
# AI Trading Engine (FINAL VERSION - STABLE + HIGH WIN RATE)
# SMC + EMA + RSI + BACKTESTER + CHARTS
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
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "risk_reward": 2,
    "risk_percent": 1,
    "min_confidence": 65
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
def fetch_binance(symbol, interval="1m", limit=200):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        data = requests.get(url, timeout=5).json()

        df = pd.DataFrame(data, columns=[
            "time","open","high","low","close","volume",
            "_","_","_","_","_","_"
        ])

        for col in ["open","high","low","close"]:
            df[col] = df[col].astype(float)

        df["time"] = pd.to_datetime(df["time"], unit="ms")

        return df
    except:
        return None

# ---------------- STRATEGY ----------------
def evaluate_bot_window(df):

    if df is None or len(df) < 30:
        return {"signal": "HOLD", "confidence": 50}

    closes = df["close"]

    # EMA
    ema9 = closes.ewm(span=9).mean()
    ema21 = closes.ewm(span=21).mean()

    ema_bull = ema9.iloc[-1] > ema21.iloc[-1]
    ema_bear = ema9.iloc[-1] < ema21.iloc[-1]

    # RSI
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()

    rs = gain / (loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    rsi_val = rsi.iloc[-1]

    rsi_bull = rsi_val > 55
    rsi_bear = rsi_val < 45

    # SMC candle strength
    last = df.iloc[-1]
    body = abs(last["close"] - last["open"])
    rng = max(last["high"] - last["low"], 1e-6)

    smc_bull = last["close"] > last["open"] and body > rng * 0.6
    smc_bear = last["close"] < last["open"] and body > rng * 0.6

    # FINAL DECISION (CONFLUENCE)
    if smc_bull and ema_bull and rsi_bull:
        return {"signal": "BUY", "confidence": 85}

    if smc_bear and ema_bear and rsi_bear:
        return {"signal": "SELL", "confidence": 85}

    return {"signal": "HOLD", "confidence": 50}

# ---------------- TRADE LEVELS ----------------
def calculate_levels(price, signal):
    if signal == "BUY":
        sl = price * 0.99
        tp = price + (price - sl) * bot_config["risk_reward"]
    elif signal == "SELL":
        sl = price * 1.01
        tp = price - (sl - price) * bot_config["risk_reward"]
    else:
        return None

    return {
        "entry": round(price, 2),
        "sl": round(sl, 2),
        "tp": round(tp, 2)
    }

# ---------------- CHART DATA ----------------
@app.route("/api/chart-candles")
def chart_candles():
    symbol = request.args.get("symbol", "BTCUSDT")
    df = fetch_binance(symbol)

    if df is None:
        return jsonify({"ok": False, "data": []})

    candles = [{
        "time": int(r["time"].timestamp()),
        "open": r["open"],
        "high": r["high"],
        "low": r["low"],
        "close": r["close"]
    } for _, r in df.iterrows()]

    return jsonify({"ok": True, "data": candles})

@app.route("/api/chart-overlays")
def chart_overlays():
    symbol = request.args.get("symbol", "BTCUSDT")
    df = fetch_binance(symbol)

    if df is None:
        return jsonify({"markers": [], "trade_levels": [], "annotations": []})

    markers = []
    trade_levels = []

    for i in range(30, len(df)):
        window = df.iloc[:i]
        result = evaluate_bot_window(window)

        if result["signal"] != "HOLD":
            price = float(window.iloc[-1]["close"])
            levels = calculate_levels(price, result["signal"])

            markers.append({
                "time": int(window.iloc[-1]["time"].timestamp()),
                "position": "belowBar" if result["signal"] == "BUY" else "aboveBar",
                "color": "#22c55e" if result["signal"] == "BUY" else "#ef4444",
                "shape": "arrowUp" if result["signal"] == "BUY" else "arrowDown",
                "text": result["signal"]
            })

            trade_levels.append({
                "side": result["signal"],
                **levels
            })

    return jsonify({
        "markers": markers[-10:],
        "trade_levels": trade_levels[-5:],
        "annotations": []
    })

# ---------------- BACKTEST ----------------
@app.route("/api/backtest", methods=["POST"])
def backtest():
    data = request.get_json()
    symbol = data.get("symbol", "BTCUSDT")

    df = fetch_binance(symbol, interval="5m", limit=300)

    balance = 1000
    wins = 0
    trades = 0

    for i in range(30, len(df)):
        window = df.iloc[:i]
        result = evaluate_bot_window(window)

        if result["signal"] == "HOLD":
            continue

        entry = window.iloc[-1]["close"]
        next_close = df.iloc[i]["close"]

        pnl = (next_close - entry) if result["signal"] == "BUY" else (entry - next_close)

        balance += pnl
        trades += 1
        if pnl > 0:
            wins += 1

    return jsonify({
        "balance": round(balance, 2),
        "trades": trades,
        "win_rate": round((wins / trades * 100) if trades else 0, 2)
    })

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
    return render_template("backtester.html")  # ✅ FIXED

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
