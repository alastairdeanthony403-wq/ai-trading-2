# ============================================================
# AI Trading Web App (UI + Signals)
# ============================================================

from flask import Flask, render_template, request, jsonify
import pandas as pd
import requests

app = Flask(__name__)

# ---------------- CONFIG ----------------
bot_config = {
    "symbol": "AAPL",
    "risk_reward": 2
}

# ---------------- DATA FETCH ----------------
def fetch_ohlcv(symbol: str):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1h"
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers)
    data = r.json()["chart"]["result"][0]

    ts = data["timestamp"]
    q = data["indicators"]["quote"][0]

    df = pd.DataFrame({
        "date": pd.to_datetime(ts, unit="s"),
        "open": q["open"],
        "high": q["high"],
        "low": q["low"],
        "close": q["close"],
        "volume": q["volume"]
    }).dropna()

    return df

# ---------------- INDICATORS ----------------
def add_indicators(df):
    df["rsi"] = 50  # simplified placeholder
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    return df.dropna()

# ---------------- SIGNAL ----------------
def generate_signal(df):
    latest = df.iloc[-1]

    price = latest["close"]
    sma20 = latest["sma20"]
    sma50 = latest["sma50"]

    # RSI calculation
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = 100 - (100 / (1 + gain / loss))
    rsi_latest = rsi.iloc[-1]

    score = 0

    # Trend
    if sma20 > sma50:
        score += 2
    else:
        score -= 2

    # RSI
    if rsi_latest < 30:
        score += 2
    elif rsi_latest > 70:
        score -= 2

    # Price position
    if price > sma20:
        score += 1
    else:
        score -= 1

    # Decision
    if score >= 3:
        signal = "BUY"
    elif score <= -3:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {
        "signal": signal,
        "price": round(price, 2),
        "rsi": round(rsi_latest, 1),
        "score": score
    }
# ---------------- ROUTES ----------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/config", methods=["POST"])
def config():
    data = request.json
    bot_config["symbol"] = data.get("symbol", "AAPL")
    bot_config["risk_reward"] = float(data.get("risk_reward", 2))
    return jsonify({"status": "updated"})


@app.route("/signal")
def signal():
    df = fetch_ohlcv(bot_config["symbol"])
    df = add_indicators(df)

    sig = generate_signal(df)

    price = sig["price"]
    rr = bot_config["risk_reward"]

    # Stop loss & take profit
    if sig["signal"] == "BUY":
        sl = round(price * 0.98, 2)
        tp = round(price + (price - sl) * rr, 2)
    elif sig["signal"] == "SELL":
        sl = round(price * 1.02, 2)
        tp = round(price - (sl - price) * rr, 2)
    else:
        sl, tp = None, None

    return jsonify({
        "symbol": bot_config["symbol"],
        "signal": sig["signal"],
        "price": price,
        "stop_loss": sl,
        "take_profit": tp
    })
    
    def send_telegram(msg):
    TOKEN = "YOUR_TOKEN"
    CHAT_ID = "8654099944:AAEuwAtfImHBnE3TlD3a3z_eWz-oBIQMLf8"

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": msg
    })


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
