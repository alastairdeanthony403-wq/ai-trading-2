# ============================================================
# AI Trading Engine (SMC + EMA + RSI + SELF-OPTIMIZING)
# ============================================================

from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import pandas as pd
import requests

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

# ============================================================
# 🔥 SELF-OPTIMIZER (KEY UPGRADE)
# ============================================================
def optimize_strategy(df):

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
                            entry = closes.iloc[i+1]
                            exit = closes.iloc[i+2]
                            if exit > entry:
                                wins += 1
                            trades += 1

                        elif ema_fast.iloc[i] < ema_slow.iloc[i] and rsi.iloc[i] < rs:
                            entry = closes.iloc[i+1]
                            exit = closes.iloc[i+2]
                            if exit < entry:
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

    return best or {"ema_fast":9,"ema_slow":21,"rsi_buy":55,"rsi_sell":45}

# ---------------- STRATEGY ----------------
def evaluate_bot_window(df):

    if df is None or len(df) < 50:
        return {"signal": "HOLD", "confidence": 50}

    closes = df["close"]

    # 🔥 USE OPTIMIZED SETTINGS
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

    signal = "HOLD"
    confidence = 50

    # 🔥 CONFLUENCE ENTRY
    if structure == "Bullish Structure":
        if ema_fast.iloc[-1] > ema_slow.iloc[-1] and rsi_val > opt["rsi_buy"] and body > rng * 0.6:
            signal = "BUY"
            confidence = 80

    if structure == "Bearish Structure":
        if ema_fast.iloc[-1] < ema_slow.iloc[-1] and rsi_val < opt["rsi_sell"] and body > rng * 0.6:
            signal = "SELL"
            confidence = 80

    return {
        "signal": signal,
        "confidence": confidence,
        "structure": structure,
        "regime": regime,
        "optimized": opt
    }

# ---------------- BACKTEST ----------------
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
                entry = df.iloc[i+1]["close"] if i+1 < len(df) else candle["close"]

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

    return jsonify(run_backtest(df))

@app.route("/api/optimize")
def api_optimize():
    symbol = request.args.get("symbol", "BTCUSDT")
    df = fetch_binance(symbol, "5m")

    return jsonify(optimize_strategy(df))

# ---------------- CHART ----------------
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

@app.route("/backtester")
def backtester():
    return render_template("backtester.html")

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
