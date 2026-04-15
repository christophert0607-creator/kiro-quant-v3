from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StrategyProfile:
    name: str
    risk_multiplier: float
    allow_long: bool
    allow_short: bool = True  # v3 Plan B: allow SHORT entries


class StrategyFactory:
    """Builds dynamic strategy controls based on volatility regime and sentiment."""

    def choose_profile(self, vix_value: float, sentiment_score: float = 0.0) -> StrategyProfile:
        # VIX-based base profile
        if vix_value >= 28:
            profile = StrategyProfile(name="defensive", risk_multiplier=0.35, allow_long=False, allow_short=False)
        elif vix_value >= 20:
            profile = StrategyProfile(name="cautious", risk_multiplier=0.65, allow_long=True, allow_short=True)
        else:
            profile = StrategyProfile(name="normal", risk_multiplier=1.0, allow_long=True, allow_short=True)

        # Sentiment-based adjustment (-1.0 to 1.0)
        # If sentiment is very bearish (<-0.5): block LONG, allow SHORT
        if sentiment_score <= -0.5:
            profile.allow_long = False
            profile.allow_short = True
            profile.risk_multiplier *= 0.5
            profile.name += "_bearish"
        # If sentiment is very bullish (>0.5): block SHORT, allow LONG
        elif sentiment_score >= 0.5:
            profile.allow_short = False
            profile.risk_multiplier *= 1.2
            profile.name += "_bullish"

        return profile

    def confidence_to_risk_pct(self, confidence: float) -> float:
        # Adjusted Kelly-inspired allocation: Max risk reduced from 10% to 7%
        # (Equivalent to moving from Kelly 0.5 to ~0.35)
        confidence = max(0.0, min(1.0, confidence))
        return 0.01 + confidence * 0.06

    def trailing_stop_by_volatility(self, volatility: float, bars_held: int) -> float:
        base = 0.02 + max(0.0, volatility) * 0.5
        time_decay = min(0.015, bars_held * 0.0005)
        return max(0.01, base - time_decay)

    def apply_commission_erosion(self, gross_pnl: float, trades: int) -> float:
        return gross_pnl - max(0, trades) * 1.99
