# ============================================================
# AI Trading Web App (SMART MONEY PRO MAX + TRADE HISTORY)
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

# ---------------- STORAGE ----------------
last_signal = None
trade_history = []
executed_trades = set()


# ---------------- DATA FETCH ----------------
def fetch_binance(symbol, interval="1h"):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=200"
        data = requests.get(url, timeout=10).json()

        df = pd.DataFrame(data, columns=[
            "time","open","high","low","close","volume",
            "_","_","_","_","_","_"
        ])

        df["date"] = pd.to_datetime(df["time"], unit="ms")
        df[["open","high","low","close","volume"]] = df[
            ["open","high","low","close","volume"]
        ].astype(float)

        return df[["date","open","high","low","close","volume"]]

    except Exception as e:
        print(f"❌ Binance error: {e}")
        return None


def fetch_yahoo(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1h"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        data = r.json()["chart"]["result"][0]

        df = pd.DataFrame({
            "date": pd.to_datetime(data["timestamp"], unit="s"),
            "open": data["indicators"]["quote"][0]["open"],
            "high": data["indicators"]["quote"][0]["high"],
            "low": data["indicators"]["quote"][0]["low"],
            "close": data["indicators"]["quote"][0]["close"],
            "volume": data["indicators"]["quote"][0]["volume"]
        }).dropna()

        return df

    except Exception as e:
        print(f"❌ Yahoo error: {e}")
        return None


def fetch_data(symbol, interval="1h"):
    return fetch_binance(symbol, interval) if "USDT" in symbol else fetch_yahoo(symbol)


# ---------------- SMART MONEY ----------------

def detect_order_block(df):
    for i in range(len(df)-3, 0, -1):
        c = df.iloc[i]
        n = df.iloc[i+1]

        if c["close"] < c["open"] and n["close"] > n["open"]:
            return ("BUY", c["low"])

        if c["close"] > c["open"] and n["close"] < n["open"]:
            return ("SELL", c["high"])

    return (None, None)


def detect_fvg(df):
    for i in range(2, len(df)):
        c1, c3 = df.iloc[i-2], df.iloc[i]

        if c1["high"] < c3["low"]:
            return ("BUY", c1["high"], c3["low"])

        if c1["low"] > c3["high"]:
            return ("SELL", c3["high"], c1["low"])

    return (None, None, None)


def get_htf_bias(symbol):
    df = fetch_data(symbol, "4h")

    if df is None or len(df) < 50:
        return 0

    high = df["high"].tail(20).max()
    low = df["low"].tail(20).min()
    price = df.iloc[-1]["close"]

    mid = (high + low) / 2
    return -1 if price > mid else 1


# ---------------- SIGNAL ----------------
def generate_signal(df, symbol):
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    price = latest["close"]

    body = abs(latest["close"] - latest["open"])
    rng = latest["high"] - latest["low"]

    upper_wick = latest["high"] - max(latest["open"], latest["close"])
    lower_wick = min(latest["open"], latest["close"]) - latest["low"]

    score = 0

    # BOS
    if latest["close"] > prev["high"]:
        score += 2
    elif latest["close"] < prev["low"]:
        score -= 2

    # Liquidity sweep
    if latest["high"] > prev["high"] and latest["close"] < prev["high"]:
        score -= 2
    if latest["low"] < prev["low"] and latest["close"] > prev["low"]:
        score += 2

    # Candle strength
    if body > rng * 0.6:
        score += 2 if latest["close"] > latest["open"] else -2

    # Wicks
    if lower_wick > body:
        score += 1
    if upper_wick > body:
        score -= 1

    # Order Block
    ob_type, ob_level = detect_order_block(df)
    if ob_type == "BUY" and price <= ob_level * 1.01:
        score += 2
    elif ob_type == "SELL" and price >= ob_level * 0.99:
        score -= 2

    # FVG
    fvg_type, fvg_low, fvg_high = detect_fvg(df)
    if fvg_type == "BUY" and price <= fvg_high:
        score += 1
    elif fvg_type == "SELL" and price >= fvg_low:
        score -= 1

    # HTF bias
    score += get_htf_bias(symbol)

    # Decision
    if score >= 4:
        signal = "BUY"
    elif score <= -4:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {"signal": signal, "price": round(price, 2), "score": score}


# ---------------- ROUTES ----------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/charts")
def charts():
    return render_template("charts.html")


@app.route("/symbols", methods=["POST"])
def update_symbols():
    data = request.json
    bot_config["symbols"] = data.get("symbols", bot_config["symbols"])
    bot_config["risk_reward"] = float(data.get("risk_reward", 2))
    return jsonify({"symbols": bot_config["symbols"]})


@app.route("/history")
def history():
    return jsonify(trade_history[-50:])


@app.route("/signal")
def signal():
    global trade_history, executed_trades

    results = []

    for symbol in bot_config["symbols"]:
        df = fetch_data(symbol)

        if df is None or len(df) < 50:
            continue

        try:
            sig = generate_signal(df, symbol)

            price = sig["price"]
            live_price = float(df.iloc[-1]["close"])
            rr = bot_config["risk_reward"]

            low = df["low"].tail(10).min()
            high = df["high"].tail(10).max()

            if sig["signal"] == "BUY":
                sl = round(low, 2)
                tp = round(price + (price - sl) * rr, 2)
                pnl = live_price - price

            elif sig["signal"] == "SELL":
                sl = round(high, 2)
                tp = round(price - (sl - price) * rr, 2)
                pnl = price - live_price

            else:
                sl, tp, pnl = None, None, 0

            result = {
                "symbol": symbol,
                "signal": sig["signal"],
                "price": price,
                "live_price": round(live_price, 2),
                "stop_loss": sl,
                "take_profit": tp,
                "score": sig["score"],
                "pnl": round(pnl, 2),
                "confidence": min(abs(sig["score"]) * 15, 100)
            }

            results.append(result)

            # 🔥 TRADE LOGGING
            trade_key = f"{symbol}_{sig['signal']}"

            if sig["signal"] in ["BUY", "SELL"] and abs(sig["score"]) >= 4:
                if trade_key not in executed_trades:
                    executed_trades.add(trade_key)

                    trade_history.append({
                        "symbol": symbol,
                        "signal": sig["signal"],
                        "entry": price,
                        "exit": round(live_price, 2),
                        "pnl": round(pnl, 2),
                        "time": str(df.iloc[-1]["date"])
                    })

        except Exception as e:
            print(f"❌ Error with {symbol}: {e}")

    if not results:
        return jsonify({"best_trade": None, "all_signals": []})

    best = sorted(results, key=lambda x: abs(x["score"]), reverse=True)[0]

    return jsonify({
        "best_trade": best,
        "all_signals": results
    })


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
