# ============================================================
# AI Trading Web App (UPGRADED + DEBUG + DYNAMIC SYMBOLS)
# ============================================================

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import pandas as pd
import requests

app = Flask(__name__)
CORS(app)

# ---------------- CONFIG ----------------
bot_config = {
    "symbols": ["^DJI", "GC=F", "EURUSD=X", "^IXIC"],
    "risk_reward": 2
}

last_signal = None


# ---------------- TELEGRAM ----------------
def send_telegram(msg):
    TOKEN = "AAEDn-8QO0nT6FlsBwl1QYJRMloIoja0Rdo"   # 🔴 REPLACE
    CHAT_ID = "8654099944"

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": msg
    })


# ---------------- DATA FETCH ----------------
def fetch_ohlcv(symbol: str):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1h"
        headers = {"User-Agent": "Mozilla/5.0"}

        r = requests.get(url, headers=headers)
        json_data = r.json()

        if not json_data["chart"]["result"]:
            print(f"❌ No data for {symbol}")
            return None

        data = json_data["chart"]["result"][0]

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
        print(f"❌ Data error for {symbol}: {e}")
        return None

# ---------------- INDICATORS ----------------
def add_indicators(df):
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss))

    return df.dropna()


# ---------------- SIGNAL ----------------
def generate_signal(df):
    latest = df.iloc[-1]

    price = latest["close"]
    sma20 = latest["sma20"]
    sma50 = latest["sma50"]
    rsi = latest["rsi"]

    score = 0

    if sma20 > sma50:
        score += 2
    else:
        score -= 2

    if rsi < 35:
        score += 1
    elif rsi > 65:
        score -= 1

    if price > sma20:
        score += 1
    else:
        score -= 1

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


# 🔧 UPDATE SYMBOL LIST
@app.route("/symbols", methods=["POST"])
def update_symbols():
    data = request.json
    bot_config["symbols"] = data.get("symbols", bot_config["symbols"])
    return jsonify({"symbols": bot_config["symbols"]})


# 🔍 DEBUG ROUTE
@app.route("/debug")
def debug():
    return jsonify({
        "symbols": bot_config["symbols"],
        "last_signal": last_signal
    })


# 📊 MAIN SIGNAL ROUTE
@app.route("/signal")
def signal():
    global last_signal

    results = []

    for symbol in bot_config["symbols"]:
        df = fetch_ohlcv(symbol)

        if df is None or len(df) < 50:
            continue

        try:
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

            print(f"📊 {symbol}: {sig['signal']} | Score: {sig['score']}")

            # 🚨 ALERT
            if sig["signal"] in ["BUY", "SELL"] and abs(sig["score"]) >= 3:
                key = f"{symbol}_{sig['signal']}"

                if key != last_signal:
                    send_telegram(
                        f"""
🚨 TRADE SIGNAL 🚨

Symbol: {symbol}
Signal: {sig['signal']}
Price: {price}

SL: {sl}
TP: {tp}
                        """
                    )
                    last_signal = key

        except Exception as e:
            print(f"❌ Error with {symbol}: {e}")

    if not results:
        return jsonify({"error": "No data available"})

    best = sorted(results, key=lambda x: abs(x["score"]), reverse=True)[0]

    return jsonify({
        "best_trade": best,
        "all_signals": results
    })


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
