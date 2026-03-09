from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StrategyProfile:
    name: str
    risk_multiplier: float
    allow_long: bool


class StrategyFactory:
    """Builds dynamic strategy controls based on volatility regime and confidence."""

    def choose_profile(self, vix_value: float) -> StrategyProfile:
        if vix_value >= 28:
            return StrategyProfile(name="defensive", risk_multiplier=0.35, allow_long=False)
        if vix_value >= 20:
            return StrategyProfile(name="cautious", risk_multiplier=0.65, allow_long=True)
        return StrategyProfile(name="normal", risk_multiplier=1.0, allow_long=True)

    def confidence_to_risk_pct(self, confidence: float) -> float:
        confidence = max(0.0, min(1.0, confidence))
        return 0.01 + confidence * 0.09

    def trailing_stop_by_volatility(self, volatility: float, bars_held: int) -> float:
        base = 0.02 + max(0.0, volatility) * 0.5
        time_decay = min(0.015, bars_held * 0.0005)
        return max(0.01, base - time_decay)

    def apply_commission_erosion(self, gross_pnl: float, trades: int) -> float:
        return gross_pnl - max(0, trades) * 1.99
