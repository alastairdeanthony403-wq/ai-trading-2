# ============================================================
# AI Trading Web App (FINAL CLEAN VERSION)
# ============================================================

from flask import Flask, render_template, request, jsonify
import pandas as pd
import requests

app = Flask(__name__)

# ---------------- CONFIG ----------------
bot_config = {
    "symbols": ["AAPL", "TSLA", "MSFT", "AMZN"],
    "risk_reward": 2
}

last_signal = None  # prevents spam alerts


# ---------------- TELEGRAM ----------------
def send_telegram(msg):
    TOKEN = "AAFtFv9C4Tkp-LmAm-giQ7Nfx9-Hp_EQjYg"   
    CHAT_ID = "8654099944"   
    url = f"https://api.telegram.org/bot{AAFtFv9C4Tkp-LmAm-giQ7Nfx9-Hp_EQjYg}/sendMessage"

    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": msg
    })


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

    # Trend
    if sma20 > sma50:
        score += 2
    else:
        score -= 2

    # RSI
    if rsi < 35:
        score += 1
    elif rsi > 65:
        score -= 1

    # Price position
    if price > sma20:
        score += 1
    else:
        score -= 1

    # ✅ HIGH CONFIDENCE DECISION
    if score >= 3:
        signal = "BUY"
    elif score <= -3:
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


@app.route("/config", methods=["POST"])
def config():
    data = request.json
    bot_config["symbols"] = data.get("symbols", ["AAPL"])
    bot_config["risk_reward"] = float(data.get("risk_reward", 2))
    return jsonify({"status": "updated"})


@app.route("/signal")
def signal():
    global last_signal

    results = []

    for symbol in bot_config["symbols"]:
        try:
            df = fetch_ohlcv(symbol)
            df = add_indicators(df)
            sig = generate_signal(df)

            price = sig["price"]
            rr = bot_config["risk_reward"]

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

            results.append(result)

            # 🚨 HIGH-CONFIDENCE TELEGRAM ALERT
            if sig["signal"] in ["BUY", "SELL"] and abs(sig["score"]) >= 3:
                key = f"{symbol}_{sig['signal']}"

                if key != last_signal:
                    send_telegram(
                        f"""
🚨 TRADE SIGNAL 🚨

Symbol: {symbol}
Signal: {sig['signal']}
Price: {price}

Stop Loss: {sl}
Take Profit: {tp}
                        """
                    )
                    last_signal = key

        except Exception as e:
            print(f"Error with {symbol}: {e}")

    # Pick best trade
    best = sorted(results, key=lambda x: abs(x["score"]), reverse=True)[0]

    return jsonify({
        "best_trade": best,
        "all_signals": results
    })


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
