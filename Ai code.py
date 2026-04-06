# ============================================================
# AI Stock Trading Tool — Pure Python (no Chrome needed)
# Requirements: pip install numpy pandas requests tensorflow
# ============================================================

import numpy as np
import pandas as pd
import requests
from collections import deque
import random

# ── 1. FETCH DATA (Yahoo Finance — no API key) ──────────────
def fetch_ohlcv(symbol: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={period}&interval={interval}&includePrePost=false"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()["chart"]["result"][0]
    ts   = data["timestamp"]
    q    = data["indicators"]["quote"][0]
    df   = pd.DataFrame({
        "date":   pd.to_datetime(ts, unit="s"),
        "open":   q["open"],
        "high":   q["high"],
        "low":    q["low"],
        "close":  q["close"],
        "volume": q["volume"],
    }).dropna().reset_index(drop=True)
    return df

# ── 2. TECHNICAL INDICATORS ─────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]

    # RSI
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))

    # MACD
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["macd"]   = ema12 - ema26
    df["signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    # Bollinger Bands
    sma20        = c.rolling(20).mean()
    std20        = c.rolling(20).std()
    df["bb_upper"] = sma20 + 2 * std20
    df["bb_lower"] = sma20 - 2 * std20

    # ATR
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - c.shift()).abs()
    lcp = (df["low"]  - c.shift()).abs()
    df["atr"] = pd.concat([hl, hcp, lcp], axis=1).max(axis=1).rolling(14).mean()

    df["sma20"] = sma20
    df["sma50"] = c.rolling(50).mean()
    df["returns"] = c.pct_change()
    return df.dropna().reset_index(drop=True)

# ── 3. BUILD FEATURE MATRIX ─────────────────────────────────
FEATURES = ["returns", "rsi", "macd", "signal", "bb_upper", "bb_lower", "atr", "sma20", "sma50"]

def build_features(df: pd.DataFrame, lookback: int = 30) -> np.ndarray:
    raw = df[FEATURES].copy()
    # Normalise each feature column with z-score
    raw = (raw - raw.mean()) / (raw.std() + 1e-9)
    X = []
    for i in range(lookback, len(raw)):
        X.append(raw.iloc[i - lookback : i].values.flatten())
    return np.array(X, dtype=np.float32)

# ── 4. TRADING ENVIRONMENT ──────────────────────────────────
class StockEnv:
    """Minimal gym-style environment for one stock."""

    def __init__(self, df: pd.DataFrame, lookback: int = 30,
                 initial_cash: float = 10_000, commission: float = 0.001):
        self.df           = df.reset_index(drop=True)
        self.features     = build_features(df, lookback)
        self.lookback     = lookback
        self.n            = len(self.features)
        self.initial_cash = initial_cash
        self.commission   = commission
        self.state_dim    = self.features.shape[1] + 3  # +pos, +cash_ratio, +unrealised
        self.reset()

    def reset(self):
        self.cash     = self.initial_cash
        self.shares   = 0
        self.step_idx = 0
        self.max_worth = self.initial_cash
        self.trades   = []
        return self._state()

    def _price(self):
        return float(self.df["close"].iloc[self.step_idx + self.lookback])

    def _worth(self):
        return self.cash + self.shares * self._price()

    def _state(self):
        feats = self.features[self.step_idx]
        price = self._price()
        pos_ratio    = (self.shares * price) / max(self._worth(), 1e-9)
        cash_ratio   = self.cash / max(self._worth(), 1e-9)
        unrealised   = 0.0
        if self.shares > 0 and self.trades:
            last_buy = next((t for t in reversed(self.trades) if t["action"] == "BUY"), None)
            if last_buy:
                unrealised = (price - last_buy["price"]) / last_buy["price"]
        return np.append(feats, [pos_ratio, cash_ratio, unrealised]).astype(np.float32)

    def step(self, action: int):
        """action: 0=BUY, 1=SELL, 2=HOLD"""
        price     = self._price()
        prev_worth = self._worth()
        trade_cost = 0.0

        if action == 0:  # BUY
            spend  = self.cash * 0.2          # use up to 20 % of cash
            qty    = int(spend // (price * (1 + self.commission)))
            if qty > 0:
                trade_cost  = qty * price * self.commission
                self.cash  -= qty * price + trade_cost
                self.shares += qty
                self.trades.append({"action": "BUY", "price": price, "qty": qty})

        elif action == 1:  # SELL
            if self.shares > 0:
                trade_cost  = self.shares * price * self.commission
                self.cash  += self.shares * price - trade_cost
                last_buy    = next((t for t in reversed(self.trades) if t["action"] == "BUY"), None)
                pnl         = (price - (last_buy["price"] if last_buy else price)) * self.shares
                self.trades.append({"action": "SELL", "price": price, "qty": self.shares, "pnl": pnl})
                self.shares = 0

        worth          = self._worth()
        self.max_worth = max(self.max_worth, worth)
        drawdown       = (self.max_worth - worth) / max(self.max_worth, 1e-9)
        ret            = (worth - prev_worth) / max(prev_worth, 1e-9)
        reward         = ret - 0.0001 * drawdown - trade_cost / max(worth, 1e-9) * 10

        self.step_idx += 1
        done = self.step_idx >= self.n - 1
        return self._state(), reward, done

    def metrics(self):
        worth   = self._worth()
        sells   = [t for t in self.trades if t["action"] == "SELL"]
        wins    = [t for t in sells if t.get("pnl", 0) > 0]
        returns = [(t.get("pnl", 0) / self.initial_cash) for t in sells]
        mean_r  = np.mean(returns) if returns else 0
        std_r   = np.std(returns)  if returns else 1e-9
        return {
            "final_worth":  round(worth, 2),
            "total_return": round((worth - self.initial_cash) / self.initial_cash * 100, 2),
            "win_rate":     round(len(wins) / max(len(sells), 1) * 100, 1),
            "sharpe":       round(mean_r / std_r * np.sqrt(252), 3),
            "num_trades":   len(self.trades),
        }

# ── 5. DQN AGENT ────────────────────────────────────────────
try:
    import tensorflow as tf
    _TF_AVAILABLE = True
except ImportError:
    _TF_AVAILABLE = False
    print("TensorFlow not found — using random agent. Install: pip install tensorflow")

class DQNAgent:
    def __init__(self, state_dim: int, n_actions: int = 3,
                 lr: float = 1e-3, gamma: float = 0.95,
                 epsilon: float = 1.0, epsilon_decay: float = 0.995,
                 epsilon_min: float = 0.05, buffer_size: int = 10_000,
                 batch_size: int = 32):
        self.n_actions     = n_actions
        self.gamma         = gamma
        self.epsilon       = epsilon
        self.epsilon_decay = epsilon_decay
        self.epsilon_min   = epsilon_min
        self.batch_size    = batch_size
        self.buffer        = deque(maxlen=buffer_size)
        self.losses        = []

        if _TF_AVAILABLE:
            self.q_net     = self._build(state_dim, n_actions, lr)
            self.target_net = self._build(state_dim, n_actions, lr)
            self._sync_target()
        self.step_count = 0

    def _build(self, state_dim, n_actions, lr):
        model = tf.keras.Sequential([
            tf.keras.layers.Dense(256, activation="relu", input_shape=(state_dim,)),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(128, activation="relu"),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(64,  activation="relu"),
            tf.keras.layers.Dense(n_actions, activation="linear"),
        ])
        model.compile(optimizer=tf.keras.optimizers.Adam(lr), loss="mse")
        return model

    def _sync_target(self):
        self.target_net.set_weights(self.q_net.get_weights())

    def act(self, state: np.ndarray) -> int:
        if not _TF_AVAILABLE or random.random() < self.epsilon:
            return random.randint(0, self.n_actions - 1)
        q = self.q_net.predict(state[None], verbose=0)[0]
        return int(np.argmax(q))

    def remember(self, s, a, r, ns, done):
        self.buffer.append((s, a, r, ns, done))

    def learn(self):
        if not _TF_AVAILABLE or len(self.buffer) < self.batch_size:
            return 0.0
        batch  = random.sample(self.buffer, self.batch_size)
        S, A, R, NS, D = map(np.array, zip(*batch))
        next_q = self.target_net.predict(NS, verbose=0)
        targets = self.q_net.predict(S, verbose=0)
        for i in range(self.batch_size):
            targets[i, A[i]] = R[i] + (0 if D[i] else self.gamma * np.max(next_q[i]))
        hist = self.q_net.fit(S, targets, epochs=1, batch_size=self.batch_size, verbose=0)
        loss = hist.history["loss"][0]
        self.losses.append(loss)
        self.step_count += 1
        if self.step_count % 100 == 0:
            self._sync_target()
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
        return loss

    def save(self, path: str = "dqn_weights.weights.h5"):
        if _TF_AVAILABLE:
            self.q_net.save_weights(path)
            print(f"  Saved weights → {path}")

    def load(self, path: str = "dqn_weights.weights.h5"):
        if _TF_AVAILABLE:
            try:
                self.q_net.load_weights(path)
                self._sync_target()
                print(f"  Loaded weights from {path}")
                return True
            except Exception:
                return False
        return False

# ── 6. PATTERN DETECTOR (rule-based, no ML needed) ──────────
def detect_patterns(df: pd.DataFrame) -> dict:
    c = df["close"].values
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    n = len(c)
    if n < 5:
        return {}

    last, prev = -1, -2
    body_last = abs(c[last] - o[last])
    rng_last  = h[last] - l[last] + 1e-9
    lower_wick = min(o[last], c[last]) - l[last]
    upper_wick = h[last] - max(o[last], c[last])

    patterns = {}

    # Doji
    patterns["doji"] = round(float(body_last / rng_last < 0.1), 2)

    # Hammer
    patterns["hammer"] = round(
        float(lower_wick > 2 * body_last and upper_wick < 0.5 * body_last), 2
    )

    # Bullish engulfing
    prev_bear = c[prev] < o[prev]
    cur_bull  = c[last] > o[last]
    patterns["bullish_engulfing"] = round(float(
        prev_bear and cur_bull and o[last] < c[prev] and c[last] > o[prev]
    ), 2)

    # Bearish engulfing
    prev_bull = c[prev] > o[prev]
    cur_bear  = c[last] < o[last]
    patterns["bearish_engulfing"] = round(float(
        prev_bull and cur_bear and o[last] > c[prev] and c[last] < o[prev]
    ), 2)

    # Double top (last 20 bars)
    w = min(20, n)
    highs = h[-w:]
    m1i = int(np.argmax(highs))
    remaining = np.where(np.arange(w) != m1i, highs, -np.inf)
    m2i = int(np.argmax(remaining))
    tol = highs[m1i] * 0.01
    patterns["double_top"] = round(float(
        abs(m1i - m2i) >= 3 and abs(highs[m1i] - highs[m2i]) < tol
    ), 2)

    # Double bottom
    lows  = l[-w:]
    b1i   = int(np.argmin(lows))
    rem2  = np.where(np.arange(w) != b1i, lows, np.inf)
    b2i   = int(np.argmin(rem2))
    tolb  = lows[b1i] * 0.01
    patterns["double_bottom"] = round(float(
        abs(b1i - b2i) >= 3 and abs(lows[b1i] - lows[b2i]) < tolb
    ), 2)

    # Uptrend / Downtrend (simple slope)
    slope = np.polyfit(range(min(10, n)), c[-min(10, n):], 1)[0]
    patterns["uptrend"]   = round(float(slope > 0), 2)
    patterns["downtrend"] = round(float(slope < 0), 2)

    return patterns

# ── 7. SIGNAL GENERATOR ─────────────────────────────────────
def generate_signal(df: pd.DataFrame) -> dict:
    """
    Combines pattern detection + indicator thresholds
    into a simple BUY / SELL / HOLD signal.
    """
    latest   = df.iloc[-1]
    patterns = detect_patterns(df)

    score = 0  # positive = bullish, negative = bearish

    # Indicator signals
    rsi = latest.get("rsi", 50)
    if rsi < 35:  score += 2   # oversold → bullish
    if rsi > 65:  score -= 2   # overbought → bearish

    if latest.get("macd", 0) > latest.get("signal", 0):
        score += 1              # MACD crossover bullish
    else:
        score -= 1

    price = latest["close"]
    if price > latest.get("sma20", price): score += 1
    if price > latest.get("sma50", price): score += 1
    if price < latest.get("sma20", price): score -= 1
    if price < latest.get("sma50", price): score -= 1

    # Pattern signals
    bullish_pats = ["hammer", "bullish_engulfing", "double_bottom", "uptrend"]
    bearish_pats = ["bearish_engulfing", "double_top", "downtrend"]
    for p in bullish_pats: score += int(patterns.get(p, 0) > 0) * 2
    for p in bearish_pats: score -= int(patterns.get(p, 0) > 0) * 2

    if score >= 3:
        action = "BUY"
    elif score <= -3:
        action = "SELL"
    else:
        action = "HOLD"

    return {
        "signal":     action,
        "score":      score,
        "price":      round(price, 2),
        "rsi":        round(rsi, 1),
        "macd":       round(float(latest.get("macd", 0)), 4),
        "patterns":   {k: v for k, v in patterns.items() if v > 0},
    }

# ── 8. BACKTESTER ────────────────────────────────────────────
def backtest(symbol: str = "AAPL", episodes: int = 50,
             lookback: int = 30, save_weights: bool = True,
             load_weights: bool = True, verbose: bool = True) -> dict:
    """
    Full backtest loop:
      1. Fetch data
      2. Build env + DQN agent
      3. Run N episodes
      4. Print metrics
    """
    print(f"\n{'='*55}")
    print(f"  AI Stock Trader — Backtesting {symbol} ({episodes} eps)")
    print(f"{'='*55}")

    # Data
    df = fetch_ohlcv(symbol)
    df = add_indicators(df)
    print(f"  Data loaded: {len(df)} bars  ({df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()})")

    # Signal from indicators
    sig = generate_signal(df)
    print(f"\n  Current Signal: [{sig['signal']}]  Price=${sig['price']}  RSI={sig['rsi']}")
    if sig["patterns"]:
        print(f"  Patterns: {sig['patterns']}")

    # Env + Agent
    env   = StockEnv(df, lookback=lookback)
    agent = DQNAgent(state_dim=env.state_dim)
    if load_weights:
        agent.load()

    best_return = -np.inf
    all_results = []

    for ep in range(1, episodes + 1):
        state = env.reset()
        done  = False
        ep_reward = 0.0

        while not done:
            action             = agent.act(state)
            next_state, r, done = env.step(action)
            agent.remember(state, action, r, next_state, done)
            agent.learn()
            state      = next_state
            ep_reward += r

        m = env.metrics()
        all_results.append(m)

        if m["total_return"] > best_return:
            best_return = m["total_return"]
            if save_weights:
                agent.save()

        if verbose and ep % max(1, episodes // 10) == 0:
            avg_ret = np.mean([r["total_return"] for r in all_results[-10:]])
            print(f"  Ep {ep:4d}/{episodes} | ε={agent.epsilon:.3f} "
                  f"| AvgReturn={avg_ret:+.2f}%  WinRate={m['win_rate']:.1f}%  "
                  f"Sharpe={m['sharpe']:.2f}  Trades={m['num_trades']}")

    # Summary
    returns = [r["total_return"] for r in all_results]
    summary = {
        "symbol":           symbol,
        "episodes":         episodes,
        "avg_return_pct":   round(np.mean(returns), 2),
        "best_return_pct":  round(np.max(returns),  2),
        "worst_return_pct": round(np.min(returns),  2),
        "positive_eps":     int(sum(r > 0 for r in returns)),
        "avg_win_rate":     round(np.mean([r["win_rate"] for r in all_results]), 1),
        "avg_sharpe":       round(np.mean([r["sharpe"]   for r in all_results]), 3),
        "final_epsilon":    round(agent.epsilon, 4),
        "signal":           sig,
    }

    print(f"\n{'─'*55}")
    print(f"  SUMMARY  {symbol}")
    print(f"  Avg Return    : {summary['avg_return_pct']:+.2f}%")
    print(f"  Best Return   : {summary['best_return_pct']:+.2f}%")
    print(f"  Avg Win Rate  : {summary['avg_win_rate']:.1f}%")
    print(f"  Avg Sharpe    : {summary['avg_sharpe']:.3f}")
    print(f"  Positive Eps  : {summary['positive_eps']}/{episodes}")
    print(f"  Final Signal  : {sig['signal']}")
    print(f"{'─'*55}\n")

    return summary

# ── 9. PAPER TRADING SIMULATION ──────────────────────────────
def paper_trade(symbol: str = "AAPL", n_ticks: int = 30,
                lookback: int = 30, initial_cash: float = 10_000) -> pd.DataFrame:
    """
    Simulates paper trading on the most-recent data slice.
    Iterates bar-by-bar and logs every decision.
    """
    print(f"\n  Paper Trading: {symbol}  ({n_ticks} ticks)")
    df     = fetch_ohlcv(symbol)
    df     = add_indicators(df)
    env    = StockEnv(df, lookback=lookback, initial_cash=initial_cash)
    agent  = DQNAgent(state_dim=env.state_dim)
    agent.load()  # use saved weights if available

    log    = []
    state  = env.reset()
    done   = False
    ACTION_NAMES = ["BUY", "SELL", "HOLD"]

    for tick in range(min(n_ticks, env.n - 1)):
        action              = agent.act(state)
        next_state, r, done = env.step(action)
        price               = env._price()
        worth               = env._worth()
        log.append({
            "tick":    tick + 1,
            "date":    str(df["date"].iloc[tick + lookback].date()),
            "action":  ACTION_NAMES[action],
            "price":   round(price, 2),
            "worth":   round(worth, 2),
            "reward":  round(r, 6),
            "epsilon": round(agent.epsilon, 4),
        })
        print(f"  Tick {tick+1:3d} | {ACTION_NAMES[action]:4s} @ ${price:8.2f} "
              f"| Worth=${worth:10.2f}  ε={agent.epsilon:.3f}")
        state = next_state
        if done:
            break

    trades_df = pd.DataFrame(log)
    m = env.metrics()
    print(f"\n  Final Worth: ${m['final_worth']:,.2f}  "
          f"Return: {m['total_return']:+.2f}%  WinRate: {m['win_rate']:.1f}%")
    return trades_df

# ── 10. ENTRY POINT ──────────────────────────────────────────
if __name__ == "__main__":
    SYMBOL   = "AAPL"
    EPISODES = 30       # increase to 500+ for real training

    # Run backtest
    results = backtest(
        symbol       = SYMBOL,
        episodes     = EPISODES,
        lookback     = 30,
        save_weights = True,
        load_weights = True,
        verbose      = True,
    )

    # Optional: paper trade with the trained agent
    trade_log = paper_trade(
        symbol       = SYMBOL,
        n_ticks      = 20,
        initial_cash = 10_000,
    )
    print(trade_log.to_string(index=False))

from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({"message": "AI trader running"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)