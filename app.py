# ============================================================
# AI Trading Engine (LEVEL 2+ UPGRADE)
# SMC + EMA + RSI + MTF + AI CONFIDENCE + SMART SIZING
# ============================================================

from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import pandas as pd
import requests
import time
from datetime import datetime

app = Flask(__name__, template_folder="/mnt/data")
CORS(app)

# ============================================================
# CONFIG
# ============================================================
bot_config = {
    "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"],
    "risk_reward": 2,
    "base_risk": 0.01,
    "starting_balance": 1000,
}

optimizer_cache = {}
state = {
    "balance": bot_config["starting_balance"],
    "open_trades": {},   # symbol -> trade
    "trade_history": [],
    "alerts": [],
    "last_signals": {},
    "last_update": 0,
}

BINANCE_BASE_URLS = [
    "https://api.binance.com",
    "https://api-gcp.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
    "https://data-api.binance.vision",
]

# ============================================================
# DATA
# ============================================================
def fetch_binance(symbol, interval="1m", limit=500):
    for base in BINANCE_BASE_URLS:
        try:
            url = f"{base}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                continue

            df = pd.DataFrame(data, columns=[
                "time", "open", "high", "low", "close", "volume",
                "close_time", "quote_asset_volume", "num_trades",
                "taker_buy_base", "taker_buy_quote", "ignore"
            ])

            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df["time"] = pd.to_datetime(df["time"], unit="ms")
            df = df.dropna().reset_index(drop=True)
            return df
        except Exception:
            continue
    return None


def candle_time_to_unix(ts):
    if isinstance(ts, pd.Timestamp):
        return int(ts.timestamp())
    return int(pd.Timestamp(ts).timestamp())


# ============================================================
# MARKET HELPERS
# ============================================================
def get_structure(df):
    sma20 = df["close"].tail(20).mean()
    last = df["close"].iloc[-1]
    if last > sma20:
        return "Bullish"
    if last < sma20:
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
    if range_pct > 1:
        return "Active"
    return "Range"


def calc_rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


# ============================================================
# OPTIMIZER
# ============================================================
def optimize_strategy(df, symbol="GLOBAL", interval="1m"):
    cache_key = f"{symbol}:{interval}"
    cached = optimizer_cache.get(cache_key)
    if cached and (time.time() - cached["last_update"] < 300):
        return cached["params"]

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
                    ema_fast = closes.ewm(span=f, adjust=False).mean()
                    ema_slow = closes.ewm(span=s, adjust=False).mean()
                    rsi = calc_rsi(closes)

                    wins = 0
                    trades = 0
                    for i in range(30, len(df) - 2):
                        if ema_fast.iloc[i] > ema_slow.iloc[i] and rsi.iloc[i] > rb:
                            if closes.iloc[i + 2] > closes.iloc[i + 1]:
                                wins += 1
                            trades += 1
                        elif ema_fast.iloc[i] < ema_slow.iloc[i] and rsi.iloc[i] < rs:
                            if closes.iloc[i + 2] < closes.iloc[i + 1]:
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
                            "rsi_sell": rs,
                        }

    if best is None:
        best = {"ema_fast": 9, "ema_slow": 21, "rsi_buy": 55, "rsi_sell": 45}

    optimizer_cache[cache_key] = {
        "params": best,
        "last_update": time.time(),
    }
    return best


# ============================================================
# SIGNAL ENGINE
# ============================================================
def evaluate_bot_window(df, symbol="BTCUSDT", interval="1m"):
    if df is None or len(df) < 50:
        return {
            "signal": "HOLD",
            "confidence": 50,
            "structure": "Range",
            "regime": "Unknown",
            "optimized": None,
            "live_price": None,
            "change_pct": 0,
        }

    closes = df["close"]
    opt = optimize_strategy(df.tail(200), symbol=symbol, interval=interval)

    ema_fast = closes.ewm(span=opt["ema_fast"], adjust=False).mean()
    ema_slow = closes.ewm(span=opt["ema_slow"], adjust=False).mean()
    rsi = calc_rsi(closes)
    rsi_val = float(rsi.iloc[-1])

    latest = df.iloc[-1]
    body = abs(latest["close"] - latest["open"])
    rng = max(latest["high"] - latest["low"], 1e-9)

    structure = get_structure(df)
    regime = get_market_regime(df)

    higher_interval = "5m" if interval in ["1m", "3m"] else "15m"
    higher_df = fetch_binance(symbol, higher_interval, 200)
    higher_structure = get_structure(higher_df) if higher_df is not None and len(higher_df) >= 20 else "Range"

    score = 0

    if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
        score += 25
    else:
        score -= 25

    if rsi_val > opt["rsi_buy"]:
        score += 20
    elif rsi_val < opt["rsi_sell"]:
        score -= 20

    if body > rng * 0.6:
        score += 20

    if structure == higher_structure:
        score += 15

    if regime == "Trending":
        score += 10

    confidence = max(0, min(100, 50 + score))

    signal = "HOLD"
    if confidence > 70:
        signal = "BUY"
    elif confidence < 30:
        signal = "SELL"

    prev_close = float(df["close"].iloc[-2]) if len(df) > 1 else float(df["close"].iloc[-1])
    last_close = float(df["close"].iloc[-1])
    change_pct = ((last_close - prev_close) / prev_close * 100) if prev_close else 0

    return {
        "symbol": symbol,
        "signal": signal,
        "confidence": round(confidence, 2),
        "structure": structure,
        "regime": regime,
        "optimized": opt,
        "live_price": round(last_close, 6),
        "change_pct": round(change_pct, 4),
    }


# ============================================================
# PAPER TRADE ENGINE FOR DASHBOARD/REALTIME
# ============================================================
def add_alert(message, alert_type="info"):
    state["alerts"].insert(0, {
        "message": message,
        "type": alert_type,
        "time": datetime.utcnow().isoformat(),
    })
    state["alerts"] = state["alerts"][:20]


def update_engine():
    # throttle to reduce API calls
    if time.time() - state["last_update"] < 5:
        return

    for symbol in bot_config["symbols"]:
        df = fetch_binance(symbol, "1m", 250)
        if df is None or len(df) < 60:
            continue

        signal_data = evaluate_bot_window(df, symbol=symbol, interval="1m")
        last_price = float(df["close"].iloc[-1])

        prev_signal = state["last_signals"].get(symbol, {}).get("signal")
        if prev_signal and prev_signal != signal_data["signal"]:
            add_alert(f"{symbol} changed from {prev_signal} to {signal_data['signal']}", "buy" if signal_data["signal"] == "BUY" else "sell" if signal_data["signal"] == "SELL" else "info")

        state["last_signals"][symbol] = signal_data

        open_trade = state["open_trades"].get(symbol)
        if open_trade:
            exit_price = None
            candle_high = float(df["high"].iloc[-1])
            candle_low = float(df["low"].iloc[-1])

            if open_trade["side"] == "BUY":
                if candle_low <= open_trade["stop_loss"]:
                    exit_price = open_trade["stop_loss"]
                elif candle_high >= open_trade["take_profit"]:
                    exit_price = open_trade["take_profit"]
                pnl = (last_price - open_trade["entry_price"]) * open_trade["size"]
            else:
                if candle_high >= open_trade["stop_loss"]:
                    exit_price = open_trade["stop_loss"]
                elif candle_low <= open_trade["take_profit"]:
                    exit_price = open_trade["take_profit"]
                pnl = (open_trade["entry_price"] - last_price) * open_trade["size"]

            open_trade["current_price"] = round(last_price, 6)
            open_trade["pnl"] = round(pnl, 2)

            if exit_price is not None:
                if open_trade["side"] == "BUY":
                    realized = (exit_price - open_trade["entry_price"]) * open_trade["size"]
                else:
                    realized = (open_trade["entry_price"] - exit_price) * open_trade["size"]

                state["balance"] += realized
                closed = {
                    **open_trade,
                    "exit_price": round(exit_price, 6),
                    "pnl": round(realized, 2),
                    "closed_at": datetime.utcnow().isoformat(),
                    "time": datetime.utcnow().isoformat(),
                }
                state["trade_history"].insert(0, closed)
                state["trade_history"] = state["trade_history"][:200]
                add_alert(f"Closed {symbol} {open_trade['side']} for {round(realized,2)}", "buy" if realized >= 0 else "sell")
                del state["open_trades"][symbol]

        if symbol not in state["open_trades"] and signal_data["signal"] in ["BUY", "SELL"]:
            entry = last_price
            if signal_data["signal"] == "BUY":
                sl = entry * 0.99
                tp = entry + (entry - sl) * bot_config["risk_reward"]
            else:
                sl = entry * 1.01
                tp = entry - (sl - entry) * bot_config["risk_reward"]

            confidence = signal_data["confidence"] / 100.0
            risk_amount = state["balance"] * bot_config["base_risk"] * max(confidence, 0.1)
            stop_distance = abs(entry - sl)
            if stop_distance <= 0:
                continue
            size = risk_amount / stop_distance

            state["open_trades"][symbol] = {
                "symbol": symbol,
                "side": signal_data["signal"],
                "entry_price": round(entry, 6),
                "current_price": round(entry, 6),
                "stop_loss": round(sl, 6),
                "take_profit": round(tp, 6),
                "size": size,
                "pnl": 0.0,
                "opened_at": datetime.utcnow().isoformat(),
                "confidence": signal_data["confidence"],
            }
            add_alert(f"Opened {symbol} {signal_data['signal']} at {round(entry,2)}", "buy" if signal_data["signal"] == "BUY" else "sell")

    state["last_update"] = time.time()


# ============================================================
# BACKTEST
# ============================================================
def run_backtest(df, symbol="BTCUSDT", interval="5m", starting_balance=1000):
    if df is None or len(df) < 60:
        return {
            "summary": {
                "net_pnl": 0,
                "final_balance": starting_balance,
                "best_trade": 0,
                "starting_balance": starting_balance,
                "total_trades": 0,
                "win_rate": 0,
            },
            "trades": [],
            "signals": [],
        }

    balance = starting_balance
    trades = []
    signals = []
    open_trade = None

    for i in range(50, len(df)):
        candle = df.iloc[i]

        if open_trade:
            high = candle["high"]
            low = candle["low"]
            exit_price = None

            if open_trade["side"] == "BUY":
                if low <= open_trade["stop_loss"]:
                    exit_price = open_trade["stop_loss"]
                elif high >= open_trade["take_profit"]:
                    exit_price = open_trade["take_profit"]
            else:
                if high >= open_trade["stop_loss"]:
                    exit_price = open_trade["stop_loss"]
                elif low <= open_trade["take_profit"]:
                    exit_price = open_trade["take_profit"]

            if exit_price is not None:
                if open_trade["side"] == "BUY":
                    pnl = (exit_price - open_trade["entry_price"]) * open_trade["size"]
                else:
                    pnl = (open_trade["entry_price"] - exit_price) * open_trade["size"]

                balance += pnl
                trades.append({
                    "side": open_trade["side"],
                    "entry_price": round(open_trade["entry_price"], 6),
                    "exit_price": round(exit_price, 6),
                    "stop_loss": round(open_trade["stop_loss"], 6),
                    "take_profit": round(open_trade["take_profit"], 6),
                    "entry_time": open_trade["entry_time"],
                    "exit_time": str(candle["time"]),
                    "pnl": round(pnl, 2),
                })
                open_trade = None

        if not open_trade:
            window = df.iloc[:i].copy()
            result = evaluate_bot_window(window, symbol=symbol, interval=interval)

            if result["signal"] in ["BUY", "SELL"]:
                entry = float(candle["close"])
                if result["signal"] == "BUY":
                    sl = entry * 0.99
                    tp = entry + (entry - sl) * bot_config["risk_reward"]
                else:
                    sl = entry * 1.01
                    tp = entry - (sl - entry) * bot_config["risk_reward"]

                confidence = result["confidence"] / 100.0
                risk_amount = balance * bot_config["base_risk"] * max(confidence, 0.1)
                size = risk_amount / abs(entry - sl)

                open_trade = {
                    "side": result["signal"],
                    "entry_price": entry,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "size": size,
                    "entry_time": str(candle["time"]),
                }
                signals.append({
                    "type": result["signal"],
                    "price": entry,
                    "time": str(candle["time"]),
                    "stop_loss": sl,
                    "take_profit": tp,
                    "confidence": result["confidence"],
                })

    wins = len([t for t in trades if t["pnl"] > 0])
    total = len(trades)
    net_pnl = balance - starting_balance
    best_trade = max([t["pnl"] for t in trades], default=0)

    return {
        "summary": {
            "net_pnl": round(net_pnl, 2),
            "final_balance": round(balance, 2),
            "best_trade": round(best_trade, 2),
            "starting_balance": starting_balance,
            "total_trades": total,
            "win_rate": round((wins / total * 100), 2) if total else 0,
        },
        "trades": trades,
        "signals": signals,
    }


# ============================================================
# CHART HELPERS
# ============================================================
def build_candle_payload(df):
    out = []
    for _, row in df.iterrows():
        out.append({
            "time": candle_time_to_unix(row["time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        })
    return out


def build_chart_overlays(df, symbol="BTCUSDT", interval="1m"):
    signal = evaluate_bot_window(df, symbol=symbol, interval=interval)
    last = df.iloc[-1]
    entry = float(last["close"])
    side = signal["signal"]

    trade_levels = []
    markers = []
    annotations = []

    if side in ["BUY", "SELL"]:
        if side == "BUY":
            sl = entry * 0.99
            tp = entry + (entry - sl) * bot_config["risk_reward"]
            marker_position = "belowBar"
            marker_color = "#22c55e"
        else:
            sl = entry * 1.01
            tp = entry - (sl - entry) * bot_config["risk_reward"]
            marker_position = "aboveBar"
            marker_color = "#ef4444"

        last_time = candle_time_to_unix(last["time"])
        start_time = candle_time_to_unix(df.iloc[max(0, len(df) - 20)]["time"])

        trade_levels.append({
            "side": side,
            "entry": round(entry, 6),
            "sl": round(sl, 6),
            "tp": round(tp, 6),
        })
        markers.append({
            "time": last_time,
            "position": marker_position,
            "color": marker_color,
            "shape": "arrowUp" if side == "BUY" else "arrowDown",
            "text": f"{side} {signal['confidence']}%",
        })
        annotations.extend([
            {
                "type": "line",
                "label": f"{side} Entry",
                "price": round(entry, 6),
                "startTime": start_time,
                "endTime": last_time,
                "color": marker_color,
            },
            {
                "type": "line",
                "label": "Stop Loss",
                "price": round(sl, 6),
                "startTime": start_time,
                "endTime": last_time,
                "color": "#f59e0b",
            },
            {
                "type": "line",
                "label": "Take Profit",
                "price": round(tp, 6),
                "startTime": start_time,
                "endTime": last_time,
                "color": "#3b82f6",
            },
        ])

    return {
        "markers": markers,
        "trade_levels": trade_levels,
        "annotations": annotations,
    }


# ============================================================
# PAGES
# ============================================================
@app.route("/")
def home():
    return render_template("unified_trading_terminal.html")

@app.route("/backtester")
def backtester_page():
    return render_template("unified_trading_terminal.html")

@app.route("/charts")
def charts_page():
    return render_template("unified_trading_terminal.html")

@app.route("/analytics")
def analytics_page():
    return render_template("unified_trading_terminal.html")

@app.route("/realtime")
def realtime_page():
    return render_template("unified_trading_terminal.html")


# ============================================================
# API FOR UNIFIED HTML
# ============================================================
@app.route("/signal")
def signal_dashboard():
    update_engine()
    all_signals = []
    for symbol in bot_config["symbols"]:
        sig = state["last_signals"].get(symbol)
        if sig:
            all_signals.append(sig)
    return jsonify({
        "balance": round(state["balance"], 2),
        "history": state["trade_history"][:50],
        "all_signals": all_signals,
    })

@app.route("/signals")
def signals_map():
    update_engine()
    return jsonify(state["last_signals"])

@app.route("/live_trades")
def live_trades():
    update_engine()
    return jsonify(list(state["open_trades"].values()))

@app.route("/alerts")
def alerts():
    update_engine()
    return jsonify(state["alerts"][:10])

@app.route("/stats")
def stats():
    update_engine()
    history = state["trade_history"]
    total = len(history)
    wins = len([t for t in history if float(t.get("pnl", 0)) > 0])
    losses = len([t for t in history if float(t.get("pnl", 0)) < 0])
    net_pnl = sum(float(t.get("pnl", 0)) for t in history)
    win_rate = (wins / total * 100) if total else 0
    return jsonify({
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 2),
        "net_pnl": round(net_pnl, 2),
    })

@app.route("/equity")
def equity():
    update_engine()
    equity_points = []
    bal = bot_config["starting_balance"]
    equity_points.append({"time": "Start", "equity": bal})
    for i, trade in enumerate(reversed(state["trade_history"])):
        bal += float(trade.get("pnl", 0))
        equity_points.append({"time": trade.get("closed_at", f"Trade {i+1}"), "equity": round(bal, 2)})
    return jsonify({"equity": equity_points})

@app.route("/history")
def history():
    update_engine()
    return jsonify({"history": state["trade_history"][:100]})

@app.route("/api/chart-candles")
def api_chart_candles():
    symbol = request.args.get("symbol", "BTCUSDT")
    interval = request.args.get("interval", "1m")
    limit = int(request.args.get("limit", 200))
    df = fetch_binance(symbol, interval, limit)
    if df is None or df.empty:
        return jsonify({"ok": False, "error": "Failed to fetch candle data", "data": []}), 500
    return jsonify({"ok": True, "data": build_candle_payload(df)})

@app.route("/api/chart-overlays")
def api_chart_overlays():
    symbol = request.args.get("symbol", "BTCUSDT")
    interval = request.args.get("interval", "1m")
    limit = int(request.args.get("limit", 200))
    df = fetch_binance(symbol, interval, limit)
    if df is None or df.empty:
        return jsonify({"ok": False, "data": {"markers": [], "trade_levels": [], "annotations": []}}), 500
    return jsonify({"ok": True, "data": build_chart_overlays(df, symbol=symbol, interval=interval)})

@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol", "BTCUSDT")
    interval = data.get("interval", "5m")
    limit = int(data.get("limit", 200))
    starting_balance = float(data.get("starting_balance", 1000))

    df = fetch_binance(symbol, interval, limit)
    if df is None or df.empty:
        return jsonify({"error": "Could not fetch data for backtest."}), 500

    return jsonify(run_backtest(df, symbol=symbol, interval=interval, starting_balance=starting_balance))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
