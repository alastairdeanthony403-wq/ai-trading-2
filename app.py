# ============================================================
# AI Trading Engine (FULL PRO VERSION - REALTIME + CHARTS API)
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
    "risk_percent": 1
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

    c.execute("""
    CREATE TABLE IF NOT EXISTS alerts (
        id TEXT,
        message TEXT,
        time TEXT
    )
    """)

    conn.commit()
    conn.close()


init_db()


# ---------------- DATA ----------------
def fetch_binance(symbol, interval="1m", limit=100):
    try:
        if not symbol or not symbol.endswith("USDT"):
            return None

        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }

        r = requests.get(url, params=params, timeout=5)

        if r.status_code != 200:
            return None

        data = r.json()

        if not isinstance(data, list) or len(data) < 2:
            return None

        df = pd.DataFrame(data, columns=[
            "time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])

        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["time"] = pd.to_datetime(df["time"], unit="ms", errors="coerce")
        df.dropna(inplace=True)

        if len(df) < 2:
            return None

        return df

    except Exception:
        return None


# ---------------- SIGNAL / ANALYSIS ----------------
def generate_signal(df):
    if df is None or len(df) < 2:
        return "HOLD"

    latest = df.iloc[-1]["close"]
    previous = df.iloc[-2]["close"]

    if latest > previous:
        return "BUY"
    elif latest < previous:
        return "SELL"
    return "HOLD"


def get_structure(df):
    if df is None or len(df) < 20:
        return "Range / Mixed"

    closes = df["close"]
    sma20 = closes.tail(20).mean()

    c0 = closes.iloc[-1]
    c1 = closes.iloc[-2]
    c2 = closes.iloc[-3]

    if c0 > sma20 and c0 > c1 > c2:
        return "Bullish Structure"
    elif c0 < sma20 and c0 < c1 < c2:
        return "Bearish Structure"
    return "Range / Mixed"


def get_market_regime(df):
    if df is None or len(df) < 20:
        return "Unknown"

    recent_high = df["high"].tail(20).max()
    recent_low = df["low"].tail(20).min()
    avg_price = df["close"].tail(20).mean()

    if avg_price == 0:
        return "Unknown"

    range_percent = ((recent_high - recent_low) / avg_price) * 100

    if range_percent > 2.5:
        return "Trending"
    elif range_percent > 1.0:
        return "Active"
    return "Range / Quiet"


def estimate_confidence(df, signal):
    if df is None or len(df) < 20:
        return 50

    closes = df["close"]
    latest = closes.iloc[-1]
    previous = closes.iloc[-2]
    sma20 = closes.tail(20).mean()
    sma5 = closes.tail(5).mean()

    confidence = 50

    if signal == "BUY":
        if latest > sma20:
            confidence += 15
        if latest > previous:
            confidence += 10
        if latest > sma5:
            confidence += 10

    elif signal == "SELL":
        if latest < sma20:
            confidence += 15
        if latest < previous:
            confidence += 10
        if latest < sma5:
            confidence += 10

    return max(35, min(95, confidence))


def get_bias_from_signal(signal):
    if signal == "BUY":
        return "Bullish"
    elif signal == "SELL":
        return "Bearish"
    return "Neutral"


def get_trade_idea(signal):
    if signal == "BUY":
        return "Pullback long / continuation"
    elif signal == "SELL":
        return "Reject highs / continuation short"
    return "Wait for clearer confirmation"


def get_symbol_summary(symbol):
    df = fetch_binance(symbol)
    if df is None:
        return None

    price = float(df.iloc[-1]["close"])
    signal = generate_signal(df)
    structure = get_structure(df)
    regime = get_market_regime(df)
    confidence = estimate_confidence(df, signal)
    bias = get_bias_from_signal(signal)
    trade_idea = get_trade_idea(signal)

    return {
        "symbol": symbol,
        "price": round(price, 2),
        "signal": signal,
        "bias": bias,
        "structure": structure,
        "regime": regime,
        "confidence": confidence,
        "trade_idea": trade_idea
    }


def get_engine_snapshot():
    for symbol in bot_config["symbols"]:
        summary = get_symbol_summary(symbol)
        if summary:
            return summary

    return {
        "symbol": "BTCUSDT",
        "price": 0,
        "signal": "HOLD",
        "bias": "Neutral",
        "structure": "Range / Mixed",
        "regime": "Unknown",
        "confidence": 50,
        "trade_idea": "No live data"
    }


# ---------------- ACCOUNT ----------------
def get_balance():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT SUM(pnl) FROM trades WHERE status='CLOSED'")
    total_pnl = c.fetchone()[0] or 0

    conn.close()
    return 10000 + total_pnl


# ---------------- ALERTS ----------------
def add_alert(message):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
    INSERT INTO alerts VALUES (?, ?, ?)
    """, (
        str(uuid.uuid4()),
        message,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()


# ---------------- TRADES ----------------
def get_open_trades():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT * FROM trades WHERE status='OPEN'")
    rows = c.fetchall()

    conn.close()
    return rows


def open_trade(symbol, signal, price):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    risk_amount = get_balance() * (bot_config["risk_percent"] / 100)

    sl = price * 0.99 if signal == "BUY" else price * 1.01
    tp = (
        price + (price - sl) * bot_config["risk_reward"]
        if signal == "BUY"
        else price - (sl - price) * bot_config["risk_reward"]
    )

    stop_distance = abs(price - sl)
    size = risk_amount / stop_distance if stop_distance else 0

    trade_id = str(uuid.uuid4())

    c.execute("""
    INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'OPEN', ?)
    """, (
        trade_id,
        symbol,
        signal,
        price,
        sl,
        tp,
        size,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()

    add_alert(f"🚀 OPEN {symbol} {signal} @ {round(price, 2)}")


def close_trade(trade_id, exit_price, pnl, symbol):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
    UPDATE trades
    SET exit=?, pnl=?, status='CLOSED'
    WHERE id=?
    """, (exit_price, pnl, trade_id))

    conn.commit()
    conn.close()

    add_alert(f"✅ CLOSED {symbol} PnL: {round(pnl, 2)}")


def update_trades(symbol, price):
    open_trades = get_open_trades()

    for trade in open_trades:
        trade_id, sym, type_, entry, sl, tp, size, _, _, _, _ = trade

        if sym != symbol:
            continue

        pnl = (price - entry) * size if type_ == "BUY" else (entry - price) * size

        if (
            (type_ == "BUY" and (price <= sl or price >= tp))
            or
            (type_ == "SELL" and (price >= sl or price <= tp))
        ):
            close_trade(trade_id, price, pnl, sym)


# ---------------- CHART DATA ----------------
def get_chart_candles(symbol="BTCUSDT", interval="1m", limit=200):
    df = fetch_binance(symbol, interval=interval, limit=limit)
    if df is None:
        return []

    candles = []
    for _, row in df.iterrows():
        candles.append({
            "time": int(row["time"].timestamp()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"])
        })
    return candles


def get_chart_signals(symbol="BTCUSDT", interval="1m", limit=200):
    df = fetch_binance(symbol, interval=interval, limit=limit)
    if df is None or len(df) < 30:
        return {
            "markers": [],
            "trade_levels": [],
            "annotations": []
        }

    markers = []
    trade_levels = []
    annotations = []

    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    times = [int(t.timestamp()) for t in df["time"]]

    for i in range(20, len(df)):
        recent_avg = df["close"].iloc[i - 10:i].mean()
        current_close = closes[i]
        prev_close = closes[i - 1]

        if prev_close <= recent_avg and current_close > recent_avg:
            entry = current_close
            sl = lows[i] * 0.995
            tp = entry + (entry - sl) * bot_config["risk_reward"]

            markers.append({
                "time": times[i],
                "position": "belowBar",
                "color": "#22c55e",
                "shape": "arrowUp",
                "text": f"BUY {symbol}"
            })

            trade_levels.append({
                "time": times[i],
                "side": "BUY",
                "entry": round(entry, 2),
                "sl": round(sl, 2),
                "tp": round(tp, 2)
            })

        elif prev_close >= recent_avg and current_close < recent_avg:
            entry = current_close
            sl = highs[i] * 1.005
            tp = entry - (sl - entry) * bot_config["risk_reward"]

            markers.append({
                "time": times[i],
                "position": "aboveBar",
                "color": "#ef4444",
                "shape": "arrowDown",
                "text": f"SELL {symbol}"
            })

            trade_levels.append({
                "time": times[i],
                "side": "SELL",
                "entry": round(entry, 2),
                "sl": round(sl, 2),
                "tp": round(tp, 2)
            })

    recent_high = max(highs[-30:])
    recent_low = min(lows[-30:])
    t1 = times[-30]
    t2 = times[-1]

    annotations.append({
        "type": "line",
        "label": "BOS High",
        "price": round(recent_high, 2),
        "color": "#3b82f6",
        "startTime": t1,
        "endTime": t2
    })

    annotations.append({
        "type": "line",
        "label": "Liquidity Low",
        "price": round(recent_low, 2),
        "color": "#f59e0b",
        "startTime": t1,
        "endTime": t2
    })

    ob_top = max(highs[-12:-8])
    ob_bottom = min(lows[-12:-8])

    annotations.append({
        "type": "rectangle",
        "label": "Order Block",
        "color": "rgba(34,197,94,0.18)",
        "borderColor": "rgba(34,197,94,0.7)",
        "startTime": times[-12],
        "endTime": times[-4],
        "top": round(ob_top, 2),
        "bottom": round(ob_bottom, 2)
    })

    fvg_top = max(highs[-8:-6])
    fvg_bottom = min(lows[-8:-6])

    annotations.append({
        "type": "rectangle",
        "label": "FVG",
        "color": "rgba(239,68,68,0.16)",
        "borderColor": "rgba(239,68,68,0.7)",
        "startTime": times[-8],
        "endTime": times[-2],
        "top": round(fvg_top, 2),
        "bottom": round(fvg_bottom, 2)
    })

    return {
        "markers": markers,
        "trade_levels": trade_levels[-8:],
        "annotations": annotations
    }


# ---------------- API ROUTES ----------------
@app.route("/live_trades")
def live_trades():
    results = []
    open_trades = get_open_trades()

    for trade in open_trades:
        trade_id, symbol, type_, entry, sl, tp, size, _, _, _, time_opened = trade

        df = fetch_binance(symbol)
        if df is None:
            continue

        price = float(df.iloc[-1]["close"])
        pnl = (price - entry) * size if type_ == "BUY" else (entry - price) * size

        results.append({
            "id": trade_id,
            "symbol": symbol,
            "type": type_,
            "entry": round(entry, 2),
            "price": round(price, 2),
            "size": round(size, 4),
            "pnl": round(pnl, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "time": time_opened
        })

    return jsonify(results)


@app.route("/chart-confirmation")
def chart_confirmation():
    tab = request.args.get("tab", "commodities").lower()
    engine = get_engine_snapshot()

    if tab == "commodities":
        data = {
            "category": "Commodities",
            "bias": engine["bias"],
            "signal": engine["signal"],
            "regime": engine["regime"],
            "confidence": engine["confidence"],
            "fx": "Neutral to weak USD" if engine["signal"] == "BUY" else "USD strength watch",
            "commodities": engine["trade_idea"],
            "indices": "Moderate risk-on" if engine["signal"] == "BUY" else "Mixed / cautious"
        }

    elif tab == "currencies":
        data = {
            "category": "Currencies",
            "bias": "Neutral" if engine["signal"] == "HOLD" else engine["bias"],
            "signal": engine["signal"],
            "regime": engine["regime"],
            "confidence": max(45, min(85, engine["confidence"] - 8)),
            "fx": "Dollar decision zone" if engine["signal"] == "HOLD" else f"Directional bias from {engine['symbol']}",
            "commodities": "No major commodity conflict",
            "indices": "Waiting for broader alignment" if engine["signal"] == "HOLD" else "Macro support present"
        }

    elif tab == "indices":
        data = {
            "category": "Indices",
            "bias": engine["bias"],
            "signal": engine["signal"],
            "regime": "Risk-on" if engine["signal"] == "BUY" else ("Risk-off" if engine["signal"] == "SELL" else "Mixed"),
            "confidence": max(50, min(90, engine["confidence"])),
            "fx": "USD not blocking upside" if engine["signal"] == "BUY" else "Defensive dollar watch",
            "commodities": "Oil and metals supportive" if engine["signal"] == "BUY" else "Mixed commodity read",
            "indices": "Broad equity strength present" if engine["signal"] == "BUY" else ("Pressure on equities" if engine["signal"] == "SELL" else "No clean trend")
        }

    else:
        data = {
            "category": "Commodities",
            "bias": engine["bias"],
            "signal": engine["signal"],
            "regime": engine["regime"],
            "confidence": engine["confidence"],
            "fx": "Neutral context",
            "commodities": engine["trade_idea"],
            "indices": "Mixed"
        }

    return jsonify(data)


@app.route("/chart-status")
def chart_status():
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    summary = get_symbol_summary(symbol)

    if not summary:
        return jsonify({
            "symbol": symbol,
            "price": 0,
            "signal": "HOLD",
            "bias": "Neutral",
            "structure": "Range / Mixed",
            "regime": "Unknown",
            "confidence": 50,
            "trade_idea": "No data available"
        })

    return jsonify(summary)


@app.route("/api/chart-candles")
def api_chart_candles():
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    interval = request.args.get("interval", "1m")
    limit = int(request.args.get("limit", 200))

    candles = get_chart_candles(symbol=symbol, interval=interval, limit=limit)
    return jsonify(candles)


@app.route("/api/chart-overlays")
def api_chart_overlays():
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    interval = request.args.get("interval", "1m")
    limit = int(request.args.get("limit", 200))

    data = get_chart_signals(symbol=symbol, interval=interval, limit=limit)
    return jsonify(data)


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


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
