from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StrategyProfile:
    name: str
    risk_multiplier: float
    allow_long: bool
    allow_short: bool = False


class StrategyFactory:
    """Builds dynamic strategy controls based on volatility regime and confidence."""

    # §9.1 regime thresholds
    _OIL_PANIC_THRESHOLD: float = 100.0   # crude oil ≥ $100 → inflation panic
    _VIX_RISK_OFF: float = 20.0           # strategy watershed
    _VIX_NEUTRAL: float = 18.5
    _VIX_RISK_ON: float = 17.0

    def choose_profile(
        self,
        vix_value: float,
        sentiment_score: float = 0.0,
        oil_price: float = 85.0,
    ) -> StrategyProfile:
        # Oil inflation panic overrides VIX (§9.1)
        if oil_price >= self._OIL_PANIC_THRESHOLD:
            return StrategyProfile(name="inflation_panic", risk_multiplier=0.1, allow_long=False)
        # VIX ≥ 20 = full defense, no longs (§9.1 watershed)
        if vix_value >= self._VIX_RISK_OFF:
            return StrategyProfile(name="risk_off", risk_multiplier=0.35, allow_long=False)
        # 18.5 ≤ VIX < 20 = stand-by, cautious longs only
        if vix_value >= self._VIX_NEUTRAL:
            return StrategyProfile(name="neutral_cautious", risk_multiplier=0.65, allow_long=True)
        # 17 ≤ VIX < 18.5 = momentum testable
        if vix_value >= self._VIX_RISK_ON:
            return StrategyProfile(name="risk_on", risk_multiplier=1.1, allow_long=True)
        # VIX < 17 = momentum aggressive
        return StrategyProfile(name="risk_on_aggressive", risk_multiplier=1.3, allow_long=True)

    def confidence_to_risk_pct(self, confidence: float) -> float:
        confidence = max(0.0, min(1.0, confidence))
        return 0.01 + confidence * 0.09

    def trailing_stop_by_volatility(self, volatility: float, bars_held: int) -> float:
        base = 0.02 + max(0.0, volatility) * 0.5
        time_decay = min(0.015, bars_held * 0.0005)
        return max(0.01, base - time_decay)

    def apply_commission_erosion(self, gross_pnl: float, trades: int) -> float:
        return gross_pnl - max(0, trades) * 1.99
