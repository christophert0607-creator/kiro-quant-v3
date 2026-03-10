from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class MonteCarloConfig:
    n_sims: int = 1000
    horizon: int = 20


class MonteCarloSimulator:
    """Random-order stress test before each trade."""

    def __init__(self, config: MonteCarloConfig | None = None) -> None:
        self.config = config or MonteCarloConfig()

    def stress_test(self, returns: pd.Series) -> dict[str, float]:
        clean = pd.to_numeric(returns, errors="coerce").dropna()
        if clean.empty:
            return {"expected": 0.0, "var95": 0.0, "cvar95": 0.0, "win_rate": 0.5}

        arr = clean.values.astype(float)
        horizon = min(self.config.horizon, len(arr))
        sims = []
        for _ in range(self.config.n_sims):
            sampled = np.random.choice(arr, size=horizon, replace=True)
            sims.append(float(np.prod(1 + sampled) - 1))

        sim_arr = np.asarray(sims)
        var95 = float(np.quantile(sim_arr, 0.05))
        tail = sim_arr[sim_arr <= var95]
        cvar95 = float(tail.mean()) if len(tail) else var95
        win_rate = float((sim_arr > 0).mean())
        return {
            "expected": float(sim_arr.mean()),
            "var95": var95,
            "cvar95": cvar95,
            "win_rate": win_rate,
        }
