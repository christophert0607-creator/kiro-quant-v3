from __future__ import annotations

import numpy as np
import pandas as pd

try:
    import gymnasium as gym
    from gymnasium import spaces
except Exception:
    gym = None
    spaces = None


class TradingEnv(gym.Env if gym is not None else object):
    metadata = {"render.modes": ["human"]}

    def __init__(self, frame: pd.DataFrame, initial_cash: float = 10000.0):
        if gym is None or spaces is None:
            raise RuntimeError("gymnasium is required for TradingEnv")

        self.df = frame.reset_index(drop=True).copy()
        self.initial_cash = float(initial_cash)
        self.idx = 0
        self.position = 0
        self.cash = self.initial_cash
        self.equity_curve: list[float] = []

        self.feature_cols = [c for c in self.df.columns if c not in ["Date", "Close"]][:27]
        while len(self.feature_cols) < 27:
            c = f"pad_{len(self.feature_cols)}"
            self.df[c] = 0.0
            self.feature_cols.append(c)

        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(29,), dtype=np.float32)
        self.action_space = spaces.Discrete(3)  # HOLD, BUY, SELL

    def _price(self) -> float:
        return float(self.df.iloc[self.idx]["Close"])

    def _obs(self) -> np.ndarray:
        row = self.df.iloc[self.idx]
        feats = pd.to_numeric(row[self.feature_cols], errors="coerce").fillna(0.0).values.astype(np.float32)
        return np.concatenate([feats, np.array([float(self.position), float(self.cash)], dtype=np.float32)])

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.idx = 0
        self.position = 0
        self.cash = self.initial_cash
        self.equity_curve = [self.initial_cash]
        return self._obs(), {}

    def step(self, action: int):
        price = self._price()
        if action == 1 and self.position == 0 and self.cash >= price:
            self.position = 1
            self.cash -= price
        elif action == 2 and self.position == 1:
            self.position = 0
            self.cash += price

        equity = self.cash + (price if self.position else 0.0)
        self.equity_curve.append(equity)

        ret = 0.0
        if len(self.equity_curve) >= 2:
            prev = self.equity_curve[-2]
            ret = (equity - prev) / prev if prev > 0 else 0.0

        returns = np.diff(self.equity_curve) / np.maximum(self.equity_curve[:-1], 1e-9)
        sharpe = float(np.mean(returns) / (np.std(returns) + 1e-9)) if len(returns) > 1 else 0.0
        peak = float(np.max(self.equity_curve)) if self.equity_curve else equity
        max_dd = float((peak - equity) / peak) if peak > 0 else 0.0
        reward = sharpe - 0.5 * max_dd + ret

        self.idx += 1
        terminated = self.idx >= len(self.df) - 1
        truncated = False
        obs = self._obs() if not terminated else np.zeros((29,), dtype=np.float32)
        info = {"equity": equity, "sharpe": sharpe, "max_drawdown": max_dd}
        return obs, float(reward), terminated, truncated, info
