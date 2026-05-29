"""PPO RL trainer — trains TradingEnv agent, evaluates Sharpe, registers to MLflow."""

import logging
import os
import tempfile
from datetime import datetime

import mlflow
import mlflow.pyfunc
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import gymnasium as gym
    from gymnasium import spaces
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv
    SB3_AVAILABLE = True
except ImportError:
    SB3_AVAILABLE = False
    logger.warning("stable-baselines3 not available — RL training disabled")


class TradingEnv:
    """Minimal gymnasium-compatible env (defined inline to avoid import cycles)."""

    if SB3_AVAILABLE:
        class _Env(gym.Env):
            metadata = {"render_modes": []}

            def __init__(self, df: pd.DataFrame):
                super().__init__()
                self.df = df.reset_index(drop=True)
                self.n = len(df)
                self.action_space = spaces.Discrete(3)
                self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(10,), dtype=np.float32)
                self.reset()

            def reset(self, *, seed=None, options=None):
                super().reset(seed=seed)
                self.idx = 20
                self.position = 0.0
                self.entry_price = 0.0
                self.pnl = 0.0
                self.max_nav = 1.0
                self.nav = 1.0
                return self._obs(), {}

            def _obs(self):
                row = self.df.iloc[self.idx]
                return np.array([
                    float(row.get("rsi", 50)) / 100.0,
                    float(row.get("macd", 0)),
                    float(row.get("vol_ratio", 1)),
                    float(row.get("momentum", 0)),
                    float(row.get("ema_dist_20", 0)),
                    float(row.get("ema_dist_50", 0)),
                    self.position,
                    self.pnl,
                    max(0.0, self.nav - self.max_nav),
                    float(row.get("atr", 0)),
                ], dtype=np.float32)

            def step(self, action):
                row = self.df.iloc[self.idx]
                price = float(row["close"])
                prev_nav = self.nav

                if action == 1 and self.position == 0:
                    self.position = 1.0
                    self.entry_price = price
                elif action == 2 and self.position == 1:
                    pnl_pct = (price - self.entry_price) / (self.entry_price + 1e-9)
                    self.nav *= (1 + pnl_pct - 0.001)
                    self.pnl = pnl_pct
                    self.position = 0.0
                    self.entry_price = 0.0

                self.max_nav = max(self.max_nav, self.nav)
                drawdown = (self.max_nav - self.nav) / (self.max_nav + 1e-9)
                reward = (self.nav - prev_nav) - 0.5 * drawdown - 0.0001

                self.idx += 1
                done = self.idx >= self.n - 1
                return self._obs(), float(reward), done, False, {}


def _compute_features(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    h = df["high"]
    lo = df["low"]
    v = df["volume"]

    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df["rsi"] = 100 - 100 / (1 + rs)

    ema12 = c.ewm(span=12).mean()
    ema26 = c.ewm(span=26).mean()
    df["macd"] = ema12 - ema26

    df["ema20"] = c.ewm(span=20).mean()
    df["ema50"] = c.ewm(span=50).mean()
    df["ema_dist_20"] = (c - df["ema20"]) / (df["ema20"] + 1e-9)
    df["ema_dist_50"] = (c - df["ema50"]) / (df["ema50"] + 1e-9)
    df["vol_ratio"] = v / (v.rolling(20).mean() + 1e-9)
    df["momentum"] = c.pct_change(5)

    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    return df.dropna()


def _df_from_rows(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def _evaluate_sharpe(model, df: pd.DataFrame) -> float:
    if not SB3_AVAILABLE:
        return 0.0
    env = TradingEnv._Env(df)
    obs, _ = env.reset()
    returns = []
    done = False
    prev_nav = 1.0
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, _, done, _, _ = env.step(int(action))
        nav = env.nav
        returns.append((nav - prev_nav) / (prev_nav + 1e-9))
        prev_nav = nav

    if not returns or np.std(returns) < 1e-9:
        return 0.0
    return float(np.mean(returns) / np.std(returns) * np.sqrt(252))


class _SB3PyfuncWrapper(mlflow.pyfunc.PythonModel):
    def load_context(self, context):
        self._model = PPO.load(context.artifacts["model_zip"])

    def predict(self, context, model_input):
        obs = np.array(model_input).astype(np.float32)
        action, _ = self._model.predict(obs, deterministic=True)
        mapping = {0: "HOLD", 1: "BUY", 2: "SELL"}
        return [mapping.get(int(a), "HOLD") for a in np.atleast_1d(action)]


async def train_rl(
    all_symbol_rows: dict[str, list[dict]],
    mlflow_uri: str,
    model_name: str,
    min_sharpe: float,
    timesteps: int,
) -> bool:
    if not SB3_AVAILABLE:
        logger.error("stable-baselines3 not installed — cannot train RL model")
        return False

    frames = []
    for symbol, rows in all_symbol_rows.items():
        if len(rows) < 60:
            continue
        df = _df_from_rows(rows)
        df = _compute_features(df)
        frames.append(df)

    if not frames:
        logger.error("No usable data for RL training")
        return False

    train_df = pd.concat(frames, ignore_index=True)
    split = int(len(train_df) * 0.8)
    train_part = train_df.iloc[:split].reset_index(drop=True)
    test_part = train_df.iloc[split:].reset_index(drop=True)

    def make_env():
        return TradingEnv._Env(train_part)

    vec_env = DummyVecEnv([make_env])

    mlflow.set_tracking_uri(mlflow_uri)
    mlflow.set_experiment("rl-trading-policy-training")

    with mlflow.start_run(run_name=f"ppo_{datetime.utcnow().strftime('%Y%m%d_%H%M')}"):
        model = PPO(
            "MlpPolicy",
            vec_env,
            learning_rate=3e-4,
            n_steps=2048,
            batch_size=64,
            n_epochs=10,
            gamma=0.99,
            gae_lambda=0.95,
            clip_range=0.2,
            verbose=0,
        )
        model.learn(total_timesteps=timesteps)

        sharpe = _evaluate_sharpe(model, test_part)
        mlflow.log_metric("sharpe_ratio", sharpe)
        mlflow.log_metric("timesteps", timesteps)
        mlflow.log_metric("train_rows", len(train_part))

        logger.info("RL Sharpe ratio: %.4f (threshold %.4f)", sharpe, min_sharpe)

        if sharpe >= min_sharpe:
            with tempfile.TemporaryDirectory() as tmpdir:
                zip_path = os.path.join(tmpdir, "ppo_model")
                model.save(zip_path)
                mlflow.pyfunc.log_model(
                    artifact_path="model",
                    python_model=_SB3PyfuncWrapper(),
                    artifacts={"model_zip": zip_path + ".zip"},
                    registered_model_name=model_name,
                )
            logger.info("Registered RL model '%s'", model_name)
            return True
        else:
            logger.warning("Sharpe %.4f below threshold %.4f — NOT registered", sharpe, min_sharpe)
            return False
