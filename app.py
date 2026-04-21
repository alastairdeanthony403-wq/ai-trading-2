# ============================================================
# AI Trading Engine (LEVEL 2+ UPGRADE)
# SMC + EMA + RSI + MTF + AI CONFIDENCE + SMART SIZING
# ============================================================

from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import pandas as pd
import requests
import time

app = Flask(__name__)
CORS(app)

# ---------------- CONFIG ----------------
bot_config = {
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "risk_reward": 2,
    "base_risk": 0.01
}

# ---------------- CACHE ----------------
optimizer_cache = {
    "params": None,
    "last_update": 0
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

        for col in ["open","high","low","close"]:
            df[col] = df[col].astype(float)

        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df

    except:
        return None

# ---------------- MARKET ----------------
def get_structure(df):
    sma20 = df["close"].tail(20).mean()
    if df["close"].iloc[-1] > sma20:
        return "Bullish"
    elif df["close"].iloc[-1] < sma20:
        return "Bearish"
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

# ============================================================
# 🔥 OPTIMIZER (CACHED — HUGE SPEED FIX)
# ============================================================
def optimize_strategy(df):

    global optimizer_cache

    # cache for 5 minutes
    if time.time() - optimizer_cache["last_update"] < 300:
        return optimizer_cache["params"]

    best = None
    best_score = -999

    ema_fast_opts = [7, 9, 12]
    ema_slow_opts = [20, 21, 30]
    rsi_buy_opts = [50, 55, 60]
    rsi_sell_opts = [40, 45, 50]

    closes = df["close"]

    for f in ema_fast_opts:
        for s in ema_slow_opts:
            for rb in rsi_buy_opts:
                for rs in rsi_sell_opts:

                    ema_fast = closes.ewm(span=f).mean()
                    ema_slow = closes.ewm(span=s).mean()

                    delta = closes.diff()
                    gain = delta.clip(lower=0).rolling(14).mean()
                    loss = -delta.clip(upper=0).rolling(14).mean()
                    rsi = 100 - (100 / (1 + (gain/(loss+1e-9))))

                    wins = 0
                    trades = 0

                    for i in range(30, len(df)-2):

                        if ema_fast.iloc[i] > ema_slow.iloc[i] and rsi.iloc[i] > rb:
                            if closes.iloc[i+2] > closes.iloc[i+1]:
                                wins += 1
                            trades += 1

                        elif ema_fast.iloc[i] < ema_slow.iloc[i] and rsi.iloc[i] < rs:
                            if closes.iloc[i+2] < closes.iloc[i+1]:
                                wins += 1
                            trades += 1

                    if trades == 0:
                        continue

                    score = (wins / trades) * trades

                    if score > best_score:
                        best_score = score
                        best = {
                            "ema_fast": f,
                            "ema_slow": s,
                            "rsi_buy": rb,
                            "rsi_sell": rs
                        }

    optimizer_cache["params"] = best
    optimizer_cache["last_update"] = time.time()

    return best

# ============================================================
# 🔥 AI SIGNAL ENGINE (UPGRADED)
# ============================================================
def evaluate_bot_window(df):

    if df is None or len(df) < 50:
        return {"signal": "HOLD", "confidence": 50}

    closes = df["close"]
    opt = optimize_strategy(df.tail(200))

    ema_fast = closes.ewm(span=opt["ema_fast"]).mean()
    ema_slow = closes.ewm(span=opt["ema_slow"]).mean()

    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rsi = 100 - (100 / (1 + (gain/(loss+1e-9))))
    rsi_val = rsi.iloc[-1]

    latest = df.iloc[-1]

    body = abs(latest["close"] - latest["open"])
    rng = latest["high"] - latest["low"]

    structure = get_structure(df)
    regime = get_market_regime(df)

    # 🔥 MULTI TIMEFRAME CONFIRMATION
    higher_df = fetch_binance("BTCUSDT", "5m", 200)
    higher_structure = get_structure(higher_df) if higher_df is not None else "Range"

    score = 0

    # EMA
    if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
        score += 25
    else:
        score -= 25

    # RSI
    if rsi_val > opt["rsi_buy"]:
        score += 20
    elif rsi_val < opt["rsi_sell"]:
        score -= 20

    # SMC candle strength
    if body > rng * 0.6:
        score += 20

    # Structure alignment
    if structure == higher_structure:
        score += 15

    # Regime filter
    if regime == "Trending":
        score += 10

    confidence = max(0, min(100, 50 + score))

    signal = "HOLD"
    if confidence > 70:
        signal = "BUY"
    elif confidence < 30:
        signal = "SELL"

    return {
        "signal": signal,
        "confidence": round(confidence, 2),
        "structure": structure,
        "regime": regime,
        "optimized": opt
    }

# ============================================================
# 🔥 BACKTEST (SMART POSITION SIZING)
# ============================================================
def run_backtest(df, starting_balance=1000):

    balance = starting_balance
    trades = []
    open_trade = None

    for i in range(50, len(df)):

        candle = df.iloc[i]

        # manage trade
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

        # open trade
        if not open_trade:
            result = evaluate_bot_window(df.iloc[:i])

            if result["signal"] in ["BUY","SELL"]:

                entry = df.iloc[i]["close"]

                if result["signal"] == "BUY":
                    sl = entry * 0.99
                    tp = entry + (entry - sl) * 2
                else:
                    sl = entry * 1.01
                    tp = entry - (sl - entry) * 2

                # 🔥 DYNAMIC POSITION SIZE
                confidence = result["confidence"] / 100
                risk = balance * bot_config["base_risk"] * confidence

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
    return jsonify(run_backtest(df))

# ---------------- PAGES ----------------
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/backtester")
def backtester():
    return render_template("backtester.html")

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
