# ============================================================
# AI Trading Web App (BINANCE + YAHOO FINAL VERSION)
# ============================================================

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import pandas as pd
import requests

app = Flask(__name__)
CORS(app)

# ---------------- CONFIG ----------------
bot_config = {
    "symbols": ["BTCUSDT", "ETHUSDT", "AAPL", "TSLA"],
    "risk_reward": 2
}

last_signal = None


# ---------------- TELEGRAM ----------------
def send_telegram(msg):
    TOKEN = "YOUR_BOT_TOKEN"
    CHAT_ID = "YOUR_CHAT_ID"

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    try:
        requests.post(url, data={
            "chat_id": CHAT_ID,
            "text": msg
        })
    except Exception as e:
        print(f"❌ Telegram error: {e}")


# ---------------- DATA FETCH ----------------

# ✅ Yahoo (stocks, forex, indices)
def fetch_yahoo(symbol: str):
    try:
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

    except Exception as e:
        print(f"❌ Yahoo error for {symbol}: {e}")
        return None


# ✅ Binance (crypto)
def fetch_binance(symbol: str):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=200"
        r = requests.get(url)
        data = r.json()

        df = pd.DataFrame(data, columns=[
            "time", "open", "high", "low", "close", "volume",
            "_", "_", "_", "_", "_", "_"
        ])

        df["date"] = pd.to_datetime(df["time"], unit="ms")
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)

        return df[["date", "open", "high", "low", "close", "volume"]]

    except Exception as e:
        print(f"❌ Binance error for {symbol}: {e}")
        return None


# 🔥 SMART ROUTER (auto choose source)
def fetch_data(symbol: str):
    if "USDT" in symbol:
        return fetch_binance(symbol)
    else:
        return fetch_yahoo(symbol)


# ---------------- INDICATORS ----------------
def add_indicators(df):
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss))

    return df.dropna()


# ---------------- SIGNAL LOGIC ----------------
def generate_signal(df):
    latest = df.iloc[-1]

    price = latest["close"]
    sma20 = latest["sma20"]
    sma50 = latest["sma50"]
    rsi = latest["rsi"]

    score = 0

    # 🔥 STRONG TREND (more strict)
    if sma20 > sma50:
        score += 2
    elif sma20 < sma50:
        score -= 2

    # 🔥 BETTER RSI FILTER
    if rsi < 30:
        score += 2   # strong oversold
    elif rsi > 70:
        score -= 2   # strong overbought

    # 🔥 PRICE CONFIRMATION
    if price > sma20:
        score += 1
    else:
        score -= 1

    # 🔥 ELITE FILTER
    if score >= 4:
        signal = "BUY"
    elif score <= -4:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {
        "signal": signal,
        "price": round(price, 2),
        "rsi": round(rsi, 1),
        "score": score
    }


# ---------------- ROUTES ----------------

@app.route("/")
def home():
    return render_template("index.html")


# 🔧 Update symbols dynamically
@app.route("/symbols", methods=["POST"])
def update_symbols():
    data = request.json
    bot_config["symbols"] = data.get("symbols", bot_config["symbols"])
    bot_config["risk_reward"] = float(data.get("risk_reward", 2))
    return jsonify({"symbols": bot_config["symbols"]})


# 🔍 Debug route
@app.route("/debug")
def debug():
    return jsonify({
        "symbols": bot_config["symbols"],
        "last_signal": last_signal
    })


# 📊 Main signal route
@app.route("/signal")
def signal():
    global last_signal

    results = []

    for symbol in bot_config["symbols"]:
        df = fetch_data(symbol)

        if df is None or len(df) < 50 or "close" not in df:
            continue

    try:
        df = add_indicators(df)
        sig = generate_signal(df)

        price = sig["price"]
        rr = bot_config["risk_reward"]

   try:
    if sig["signal"] == "BUY":
        sl = round(price * 0.98, 2)
        tp = round(price + (price - sl) * rr, 2)
    elif sig["signal"] == "SELL":
        sl = round(price * 1.02, 2)
        tp = round(price - (sl - price) * rr, 2)
    else:
        sl, tp = None, None

    result = {
        "symbol": symbol,
        "signal": sig["signal"],
        "price": price,
        "stop_loss": sl,
        "take_profit": tp,
        "score": sig["score"]
    }

    # ✅ ONLY KEEP STRONG TRADES
    if abs(sig["score"]) >= 4:
        results.append(result)

        print(f"🔥 {symbol}: {sig['signal']} | Score: {sig['score']}")

except Exception as e:
    print(f"❌ Error with {symbol}: {e}")
        # 🚨 HIGH CONFIDENCE ALERT
        if sig["signal"] in ["BUY", "SELL"]:
            key = f"{symbol}_{sig['signal']}"

            if key != last_signal:
                send_telegram(f"""
🚨 TRADE SIGNAL 🚨

Symbol: {symbol}
Signal: {sig['signal']}
Price: {price}

SL: {sl}
TP: {tp}
                """)
                last_signal = key

except Exception as e:
    print(f"❌ Error with {symbol}: {e}")

if not results:
    return jsonify({
        "message": "No high-confidence trades right now",
        "all_signals": []
    })

    best = sorted(results, key=lambda x: abs(x["score"]), reverse=True)[0]

    return jsonify({
        "best_trade": best,
        "all_signals": results
    })


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
