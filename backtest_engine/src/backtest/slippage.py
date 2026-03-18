from __future__ import annotations

from typing import Any, Dict


def estimate_slippage_rate(backtest_cfg: Dict[str, Any]) -> float:
    slippage = backtest_cfg.get("slippage", {})
    base_rate = float(slippage.get("base_rate", 0.0))
    size_impact = float(slippage.get("size_impact", 0.0))
    volatility_impact = float(slippage.get("volatility_impact", 0.0))

    # For now, return the base rate; size/volatility impacts are applied in later phases.
    return base_rate + size_impact + volatility_impact