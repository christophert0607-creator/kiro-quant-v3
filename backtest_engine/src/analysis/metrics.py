from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


def compute_metrics(equity: pd.Series, returns: pd.Series, periods_per_year: int = 252) -> Dict[str, float]:
    if len(equity) == 0:
        return {}

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    ann_factor = periods_per_year / max(len(returns), 1)
    annualized_return = (1.0 + total_return) ** ann_factor - 1.0

    ret_std = returns.std()
    sharpe = float(np.sqrt(periods_per_year) * returns.mean() / ret_std) if ret_std != 0 else 0.0

    downside = returns[returns < 0]
    downside_std = downside.std()
    sortino = float(np.sqrt(periods_per_year) * returns.mean() / downside_std) if downside_std != 0 else 0.0

    running_max = equity.cummax()
    drawdown = (equity / running_max) - 1.0
    max_dd = float(drawdown.min())
    calmar = float(annualized_return / abs(max_dd)) if max_dd != 0 else 0.0

    win_rate = float((returns > 0).mean()) if len(returns) > 0 else 0.0

    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "win_rate": win_rate,
        "max_drawdown": max_dd,
    }