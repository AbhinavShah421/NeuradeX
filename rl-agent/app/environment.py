"""Trading gym environment for RL agent training."""

import numpy as np
import gymnasium as gym
from gymnasium import spaces


class TradingEnv(gym.Env):
    """
    Single-symbol trading environment.
    State: [rsi, macd_hist, bb_position, volume_ratio, vwap_dist, ema_dist_20,
            ema_dist_50, momentum_5d, volatility_14d, current_position]
    Action: 0=HOLD, 1=BUY, 2=SELL
    Reward: pnl_pct - 0.5*max_drawdown - 0.001 (transaction cost)
    """

    metadata = {"render_modes": []}

    def __init__(self, candles: list[dict], initial_capital: float = 100_000.0):
        super().__init__()
        self.candles = candles
        self.initial_capital = initial_capital
        self.n_features = 10

        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(self.n_features,), dtype=np.float32
        )
        self.action_space = spaces.Discrete(3)

        self._reset_state()

    def _reset_state(self):
        self.current_step = 20
        self.capital = self.initial_capital
        self.position = 0       # 0=none, 1=long
        self.entry_price = 0.0
        self.peak_capital = self.initial_capital
        self.max_drawdown = 0.0

    def _get_obs(self) -> np.ndarray:
        if self.current_step >= len(self.candles):
            return np.zeros(self.n_features, dtype=np.float32)

        closes = [float(c.get("close", 0)) for c in self.candles[max(0, self.current_step-20):self.current_step+1]]
        if len(closes) < 5:
            return np.zeros(self.n_features, dtype=np.float32)

        arr = np.array(closes)
        close = arr[-1]

        delta = np.diff(arr)
        gain = np.where(delta > 0, delta, 0).mean()
        loss = np.where(delta < 0, -delta, 0).mean()
        rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))

        ema12 = arr[-min(12, len(arr)):].mean()
        ema26 = arr.mean()
        macd = (ema12 - ema26) / (close + 1e-9)

        vol = arr.std() / (close + 1e-9) * 100
        momentum = (arr[-1] / (arr[-5] + 1e-9) - 1) * 100 if len(arr) >= 5 else 0.0

        current_c = self.candles[self.current_step]
        volume = float(current_c.get("volume", 1))
        vol_sma = np.array([float(c.get("volume", 1)) for c in self.candles[max(0, self.current_step-20):self.current_step+1]]).mean()
        vol_ratio = volume / (vol_sma + 1e-9)

        ema20 = arr[-min(20, len(arr)):].mean()
        ema50 = arr[-min(50, len(arr)):].mean() if len(arr) >= 10 else ema20
        ema_dist_20 = (close - ema20) / (close + 1e-9) * 100
        ema_dist_50 = (close - ema50) / (close + 1e-9) * 100

        pnl = 0.0
        if self.position == 1 and self.entry_price > 0:
            pnl = (close - self.entry_price) / self.entry_price * 100

        obs = np.array([
            rsi / 100.0,
            macd,
            vol,
            momentum / 100.0,
            vol_ratio,
            ema_dist_20 / 100.0,
            ema_dist_50 / 100.0,
            float(self.position),
            pnl / 100.0,
            self.max_drawdown,
        ], dtype=np.float32)

        return np.clip(obs, -10, 10)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self._reset_state()
        return self._get_obs(), {}

    def step(self, action: int):
        if self.current_step >= len(self.candles) - 1:
            return self._get_obs(), 0.0, True, False, {}

        price = float(self.candles[self.current_step].get("close", 0))
        reward = -0.001   # small time penalty

        if action == 1 and self.position == 0:  # BUY
            self.position = 1
            self.entry_price = price
        elif action == 2 and self.position == 1:  # SELL
            pnl_pct = (price - self.entry_price) / (self.entry_price + 1e-9)
            reward = pnl_pct - 0.001   # pnl minus transaction cost
            self.capital *= (1 + pnl_pct)
            self.position = 0
            self.entry_price = 0.0

        if self.capital > self.peak_capital:
            self.peak_capital = self.capital
        drawdown = (self.peak_capital - self.capital) / (self.peak_capital + 1e-9)
        self.max_drawdown = max(self.max_drawdown, drawdown)
        reward -= 0.5 * drawdown

        self.current_step += 1
        done = self.current_step >= len(self.candles) - 1

        return self._get_obs(), float(reward), done, False, {}
