"""Trade-quality scoring for V3 live trading.

This module is intentionally independent from the broker and the live loop so it
can be unit-tested and used in shadow mode before enforcing any order blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Mapping, Sequence


class TradeQualityDecision(str, Enum):
    ACCEPT = "ACCEPT"
    SHADOW_ACCEPT = "SHADOW_ACCEPT"
    REJECT = "REJECT"


@dataclass(frozen=True)
class PositionContext:
    current_qty: int = 0
    current_notional: float = 0.0
    cap_remaining: float | None = None
    lot_size: int = 1


@dataclass(frozen=True)
class RecentStats:
    win_rate: float | None = None
    prediction_mae_pct: float | None = None
    turnover_penalty: float = 0.0


@dataclass(frozen=True)
class TradeQualityInput:
    symbol: str
    market: str
    action: str
    prediction: float
    current_price: float
    confidence: float
    indicators: Mapping[str, float] = field(default_factory=dict)
    position: PositionContext = field(default_factory=PositionContext)
    recent: RecentStats = field(default_factory=RecentStats)
    min_score: float = 0.65
    shadow_min_score: float = 0.50
    turnover_penalty_multiplier: float = 1.0


@dataclass(frozen=True)
class TradeQualityResult:
    decision: TradeQualityDecision
    score: float
    reasons: list[str]
    components: dict[str, float]

    @property
    def allows_entry(self) -> bool:
        return self.decision in {TradeQualityDecision.ACCEPT, TradeQualityDecision.SHADOW_ACCEPT}


def _finite(value: object, default: float = 0.0) -> float:
    try:
        out = float(value)  # type: ignore[arg-type]
    except Exception:
        return default
    return out if isfinite(out) else default


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class TradeQualityFilter:
    """Deterministic scorer for pre-order trade quality.

    The score is not a performance claim. It is a conservative filter designed
    to reduce weak entries, over-cap concentration, and turnover-heavy signals.
    """

    def score(self, inp: TradeQualityInput) -> TradeQualityResult:
        reasons: list[str] = []
        symbol = str(inp.symbol).upper()
        market = str(inp.market or ("HK" if symbol.endswith(".HK") else "US")).upper()
        action = str(inp.action or "BUY").upper()
        price = max(_finite(inp.current_price), 1e-9)
        prediction = _finite(inp.prediction, price)
        confidence = _clamp(_finite(inp.confidence))
        predicted_move = (prediction - price) / price
        direction_ok = predicted_move > 0 if action in {"BUY", "COVER"} else predicted_move < 0

        if confidence < 0.10:
            reasons.append("low_confidence")
        if not direction_ok:
            reasons.append("prediction_direction_mismatch")

        cap_remaining = inp.position.cap_remaining
        if cap_remaining is not None and cap_remaining <= 0:
            reasons.append("position_cap_exhausted")
        lot_size = max(1, int(inp.position.lot_size or 1))
        if market == "HK" and cap_remaining is not None:
            max_lot_qty = int(cap_remaining / price) // lot_size * lot_size
            if max_lot_qty <= 0:
                reasons.append("hk_lot_cap_insufficient")

        confidence_score = confidence
        # 2% predicted move is already a strong live signal; cap above it.
        move_strength_score = _clamp(abs(predicted_move) / 0.02)

        indicators = dict(inp.indicators or {})
        rsi = _finite(indicators.get("RSI_14"), 50.0)
        macd_hist = _finite(indicators.get("MACD_HIST"), 0.0)
        bb_pos = _finite(indicators.get("BB_POSITION"), 0.5)
        technical_alignment_score = 0.5
        if action in {"BUY", "COVER"}:
            technical_alignment_score += 0.20 if macd_hist >= 0 else -0.15
            technical_alignment_score += 0.15 if 25 <= rsi <= 65 else -0.10
            technical_alignment_score += 0.10 if 0.0 <= bb_pos <= 0.70 else -0.05
        else:
            technical_alignment_score += 0.20 if macd_hist <= 0 else -0.15
            technical_alignment_score += 0.15 if 35 <= rsi <= 80 else -0.10
            technical_alignment_score += 0.10 if 0.30 <= bb_pos <= 1.0 else -0.05
        technical_alignment_score = _clamp(technical_alignment_score)

        win_rate = inp.recent.win_rate
        if win_rate is None:
            recent_symbol_health_score = 0.50
        else:
            recent_symbol_health_score = _clamp((_finite(win_rate) - 0.35) / 0.35)
        mae_pct = inp.recent.prediction_mae_pct
        if mae_pct is not None:
            recent_symbol_health_score *= _clamp(1.0 - _finite(mae_pct) / 0.10)

        atr = _finite(indicators.get("ATR_14"), 0.0)
        atr_pct = atr / price if atr > 0 else 0.0
        # Prefer some volatility but reject extreme noise.
        volatility_sanity_score = 0.70 if atr_pct == 0 else _clamp(1.0 - max(0.0, atr_pct - 0.05) / 0.15)

        if cap_remaining is None:
            concentration_safety_score = 0.70
        else:
            # Strong if at least 1% of notional capacity is still available.
            concentration_safety_score = _clamp(_finite(cap_remaining) / max(price * lot_size, 1.0))
        if inp.position.current_qty > 0:
            concentration_safety_score *= 0.5

        turnover_penalty = _clamp(_finite(inp.recent.turnover_penalty) * max(0.0, inp.turnover_penalty_multiplier))
        if turnover_penalty >= 0.75:
            reasons.append("high_turnover_penalty")

        components = {
            "confidence": confidence_score,
            "move_strength": move_strength_score,
            "technical_alignment": technical_alignment_score,
            "recent_symbol_health": recent_symbol_health_score,
            "volatility_sanity": volatility_sanity_score,
            "concentration_safety": concentration_safety_score,
            "turnover_penalty": turnover_penalty,
            "predicted_move": predicted_move,
        }
        raw_score = (
            0.30 * confidence_score
            + 0.20 * move_strength_score
            + 0.15 * technical_alignment_score
            + 0.15 * recent_symbol_health_score
            + 0.10 * volatility_sanity_score
            + 0.10 * concentration_safety_score
            - turnover_penalty
        )
        score = _clamp(raw_score)

        hard_reject_reasons = {
            "position_cap_exhausted",
            "hk_lot_cap_insufficient",
            "prediction_direction_mismatch",
            "low_confidence",
            "high_turnover_penalty",
        }
        if any(r in hard_reject_reasons for r in reasons) or score < inp.shadow_min_score:
            decision = TradeQualityDecision.REJECT
        elif score >= inp.min_score:
            decision = TradeQualityDecision.ACCEPT
        else:
            decision = TradeQualityDecision.SHADOW_ACCEPT
            reasons.append("below_enforce_threshold")

        if not reasons:
            reasons.append("quality_ok")

        return TradeQualityResult(
            decision=decision,
            score=round(score, 6),
            reasons=reasons,
            components={k: round(float(v), 6) for k, v in components.items()},
        )
