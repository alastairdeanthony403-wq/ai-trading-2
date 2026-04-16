# ============================================================
# AI Trading Engine (REAL TRADE TRACKING VERSION)
# ============================================================

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import pandas as pd
import requests
import uuid

app = Flask(__name__)
CORS(app)

# ---------------- CONFIG ----------------
bot_config = {
    "symbols": ["BTCUSDT", "ETHUSDT", "AAPL"],
    "risk_reward": 2,
    "risk_percent": 1
}

# ---------------- ACCOUNT ----------------
account = {
    "balance": 10000,
    "open_trades": [],
    "trade_history": []
}


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


# ---------------- SIGNAL (simple for now) ----------------
def generate_signal(df):
    price = df.iloc[-1]["close"]
    prev = df.iloc[-2]["close"]

    if price > prev:
        return "BUY"
    elif price < prev:
        return "SELL"
    return "HOLD"


# ---------------- TRADE ENGINE ----------------

def open_trade(symbol, signal, price):
    risk_amount = account["balance"] * (bot_config["risk_percent"] / 100)

    sl = price * 0.99 if signal == "BUY" else price * 1.01
    tp = price + (price - sl) * bot_config["risk_reward"] if signal == "BUY" else price - (sl - price) * bot_config["risk_reward"]

    stop_distance = abs(price - sl)
    size = risk_amount / stop_distance if stop_distance else 0

    trade = {
        "id": str(uuid.uuid4()),
        "symbol": symbol,
        "type": signal,
        "entry": price,
        "sl": sl,
        "tp": tp,
        "size": size,
        "status": "OPEN"
    }

    account["open_trades"].append(trade)


def update_trades(symbol, price):
    for trade in account["open_trades"][:]:

        if trade["symbol"] != symbol:
            continue

        pnl = (price - trade["entry"]) * trade["size"] if trade["type"] == "BUY" else (trade["entry"] - price) * trade["size"]

        # CLOSE CONDITIONS
        if (trade["type"] == "BUY" and (price <= trade["sl"] or price >= trade["tp"])) or \
           (trade["type"] == "SELL" and (price >= trade["sl"] or price <= trade["tp"])):

            trade["exit"] = price
            trade["pnl"] = round(pnl, 2)
            trade["status"] = "CLOSED"

            account["balance"] += trade["pnl"]

            account["trade_history"].append(trade)
            account["open_trades"].remove(trade)


# ---------------- ROUTES ----------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/signal")
def signal():

    results = []

    for symbol in bot_config["symbols"]:

        df = fetch_binance(symbol)
        price = df.iloc[-1]["close"]

        sig = generate_signal(df)

        # UPDATE EXISTING TRADES
        update_trades(symbol, price)

        # OPEN NEW TRADE (if none open for symbol)
        if sig in ["BUY", "SELL"] and not any(t["symbol"] == symbol for t in account["open_trades"]):
            open_trade(symbol, sig, price)

        # GET CURRENT PNL
        open_trade_obj = next((t for t in account["open_trades"] if t["symbol"] == symbol), None)

        pnl = 0
        if open_trade_obj:
            if open_trade_obj["type"] == "BUY":
                pnl = (price - open_trade_obj["entry"]) * open_trade_obj["size"]
            else:
                pnl = (open_trade_obj["entry"] - price) * open_trade_obj["size"]

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
        "all_signals": results,
        "balance": account["balance"],
        "history": account["trade_history"]
    })


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
