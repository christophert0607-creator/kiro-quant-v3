from __future__ import annotations

from typing import Dict

import pandas as pd


def max_drawdown(equity: pd.Series) -> float:
    running_max = equity.cummax()
    drawdown = (equity / running_max) - 1.0
    return float(drawdown.min())


def check_daily_loss(equity: float, daily_start_equity: float, daily_loss_limit: float) -> bool:
    if daily_start_equity <= 0:
        return False
    return equity < daily_start_equity * (1.0 - abs(daily_loss_limit))


def check_max_drawdown_current(equity: float, peak_equity: float, max_drawdown_limit: float) -> bool:
    if peak_equity <= 0:
        return False
    return equity < peak_equity * (1.0 - abs(max_drawdown_limit))


def check_risk_limits(equity: pd.Series, max_drawdown_limit: float) -> bool:
    return max_drawdown(equity) >= -abs(max_drawdown_limit)


def summarize_risk_state(
    equity: float,
    daily_start_equity: float,
    peak_equity: float,
    daily_loss_limit: float,
    max_drawdown_limit: float,
) -> Dict[str, bool]:
    return {
        "daily_loss_breached": check_daily_loss(equity, daily_start_equity, daily_loss_limit),
        "max_drawdown_breached": check_max_drawdown_current(equity, peak_equity, max_drawdown_limit),
    }