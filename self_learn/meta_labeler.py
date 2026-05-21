"""
Meta-Labeling Decision Engine — Phase 2 Core
=============================================
Uses historical prediction accuracy (MAE + directional accuracy) per symbol
to confirm, reject, or reverse base strategy signals.

Design (meta_010):
    Input:  base_signal {symbol, action, entry_price}
            prediction  {predicted_price, confidence}
            symbol_mae  {mae, directional_accuracy}
    Output: CONFIRM / REJECT / REVERSE

Usage:
    from self_learn.meta_labeler import MetaLabeler, should_take_trade
    decision = should_take_trade(symbol="9988.HK", action="BUY", entry_price=120.0,
                                  predicted_price=125.0, confidence=0.72)
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from self_learn.models import (
    get_prediction_accuracy,
    get_latest_prediction,
    get_stats,
)


# ─── Decision Types ────────────────────────────────────────────────────────────

class Decision(str, Enum):
    """Meta-labeling decision for a base strategy signal."""
    CONFIRM  = "CONFIRM"   # Take the trade — high confidence signal
    REJECT   = "REJECT"    # Skip the trade — model predicts poor outcome
    REVERSE  = "REVERSE"   # Do the opposite instead
    NO_DATA  = "NO_DATA"   # Not enough data to decide — skip safely


# ─── Thresholds (tunable via env) ──────────────────────────────────────────────

import os

# Minimum directional accuracy to CONFIRM a signal (0.0–1.0)
DIR_ACC_CONFIRM_THRESHOLD  = float(os.getenv("META_DIR_ACC_CONFIRM",  "0.55"))
# Minimum directional accuracy to avoid REVERSE (below this → REVERSE)
DIR_ACC_REVERSE_THRESHOLD  = float(os.getenv("META_DIR_ACC_REVERSE",  "0.40"))
# Maximum MAE (in price units) to CONFIRM a high-confidence signal
MAE_CONFIRM_THRESHOLD      = float(os.getenv("META_MAE_CONFIRM",     "5.0"))
# Minimum model confidence to override directional accuracy
CONFIDENCE_OVERRIDE_THRESHOLD = float(os.getenv("META_CONFIDENCE_OVERRIDE", "0.80"))


# ─── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class SymbolAccuracy:
    """Per-symbol prediction accuracy metrics."""
    symbol: str
    mae: float                    # Mean Absolute Error in price units
    directional_accuracy: float   # Fraction correct direction (0.0–1.0)
    sample_size: int              # Number of closed trades backing this


@dataclass
class SignalContext:
    """Context for a single signal evaluation."""
    symbol: str
    action: str          # BUY or SELL
    entry_price: float
    predicted_price: float
    confidence: float
    symbol_accuracy: Optional[SymbolAccuracy] = None


@dataclass
class MetaDecision:
    """Output of meta-labeling decision."""
    decision: Decision
    confidence: float           # 0.0–1.0 confidence in this decision
    reason: str                 # Human-readable explanation
    overrides_base_signal: bool  # True if this differs from base signal action
    symbol_accuracy: Optional[SymbolAccuracy] = None


# ─── Core Decision Engine ──────────────────────────────────────────────────────

def compute_symbol_accuracy(symbol: str, window: int = 20) -> SymbolAccuracy:
    """Compute MAE and directional accuracy for a symbol from closed outcomes."""
    mae, dir_acc = get_prediction_accuracy(symbol, window=window)
    return SymbolAccuracy(
        symbol=symbol,
        mae=mae,
        directional_accuracy=dir_acc,
        sample_size=window,  # approximate
    )


def evaluate_signal(ctx: SignalContext) -> MetaDecision:
    """Core meta-labeling logic — decide whether to follow the base signal."""
    acc = ctx.symbol_accuracy

    # ── Case: No historical data ─────────────────────────────────────────────
    if acc is None or acc.directional_accuracy == 0.5:
        return MetaDecision(
            decision=Decision.NO_DATA,
            confidence=0.0,
            reason="No closed trade history for this symbol — cannot evaluate",
            overrides_base_signal=False,
            symbol_accuracy=acc,
        )

    dir_acc = acc.directional_accuracy
    mae    = acc.mae
    conf   = ctx.confidence

    # ── Case: Very high confidence model override ───────────────────────────
    if conf >= CONFIDENCE_OVERRIDE_THRESHOLD and dir_acc >= DIR_ACC_CONFIRM_THRESHOLD:
        return MetaDecision(
            decision=Decision.CONFIRM,
            confidence=conf,
            reason=f"High model confidence ({conf:.0%}) overrides directional accuracy ({dir_acc:.0%})",
            overrides_base_signal=False,
            symbol_accuracy=acc,
        )

    # ── Case: Strong directional accuracy → CONFIRM ────────────────────────
    if dir_acc >= DIR_ACC_CONFIRM_THRESHOLD:
        # Low MAE reinforces confidence
        if mae <= MAE_CONFIRM_THRESHOLD:
            reason = f"Dir acc {dir_acc:.0%} ≥ {DIR_ACC_CONFIRM_THRESHOLD:.0%} AND MAE {mae:.2f} ≤ {MAE_CONFIRM_THRESHOLD:.2f}"
        else:
            reason = f"Dir acc {dir_acc:.0%} ≥ {DIR_ACC_CONFIRM_THRESHOLD:.0%} (high MAE {mae:.2f} noted)"
        return MetaDecision(
            decision=Decision.CONFIRM,
            confidence=dir_acc,  # use dir_acc as confidence proxy
            reason=reason,
            overrides_base_signal=False,
            symbol_accuracy=acc,
        )

    # ── Case: Very poor directional accuracy → REVERSE ─────────────────────
    if dir_acc <= DIR_ACC_REVERSE_THRESHOLD:
        reason = f"Dir acc {dir_acc:.0%} ≤ {DIR_ACC_REVERSE_THRESHOLD:.0%} — model predicts opposite direction"
        return MetaDecision(
            decision=Decision.REVERSE,
            confidence=(0.5 - dir_acc),  # stronger reversal confidence when dir_acc is lower
            reason=reason,
            overrides_base_signal=True,
            symbol_accuracy=acc,
        )

    # ── Case: Middle ground → REJECT (not confident enough) ─────────────────
    return MetaDecision(
        decision=Decision.REJECT,
        confidence=abs(dir_acc - 0.5) * 2,  # 0 at 0.5, 1 at 0.0 or 1.0
        reason=f"Dir acc {dir_acc:.0%} in uncertain zone — base signal rejected",
        overrides_base_signal=False,
        symbol_accuracy=acc,
    )


def should_take_trade(
    symbol: str,
    action: str,
    entry_price: float,
    predicted_price: float,
    confidence: float,
    acc_window: int = 20,
) -> MetaDecision:
    """Public API: Should we take this base strategy signal?

    Args:
        symbol:          Ticker (e.g. "9988.HK")
        action:          "BUY" or "SELL" from base strategy
        entry_price:     Base strategy entry price
        predicted_price: ML model predicted future price
        confidence:      ML model confidence (0.0–1.0)
        acc_window:      Lookback window for computing symbol accuracy

    Returns:
        MetaDecision with CONFIRM/REJECT/REVERSE/NO_DATA
    """
    # Fetch symbol historical accuracy
    sym_acc = compute_symbol_accuracy(symbol, window=acc_window)

    ctx = SignalContext(
        symbol=symbol,
        action=action,
        entry_price=entry_price,
        predicted_price=predicted_price,
        confidence=confidence,
        symbol_accuracy=sym_acc,
    )

    return evaluate_signal(ctx)


# ─── Batch Evaluation ──────────────────────────────────────────────────────────

def evaluate_open_signals() -> list[MetaDecision]:
    """Evaluate all currently open signals with meta-labeling.

    Useful for auditing whether historical signals would have been filtered.
    """
    from self_learn.models import get_open_signals as _get_open_signals

    decisions = []
    for sig in _get_open_signals():
        # Get linked prediction if available
        pred = None
        if sig.prediction_id:
            preds = get_latest_prediction(sig.symbol, limit=1)
            pred = preds[0] if preds else None

        predicted_price = pred.get("predicted_price") if pred else None
        confidence      = pred.get("confidence")      if pred else None

        # Only evaluate if we have ML data
        if predicted_price is None:
            decisions.append(MetaDecision(
                decision=Decision.NO_DATA,
                confidence=0.0,
                reason=f"Signal {sig.id} has no linked prediction",
                overrides_base_signal=False,
                symbol_accuracy=None,
            ))
            continue

        meta = should_take_trade(
            symbol=sig.symbol,
            action=sig.action,
            entry_price=sig.entry_price or 0.0,
            predicted_price=predicted_price,
            confidence=confidence or 0.5,
        )
        decisions.append(meta)

    return decisions


# ─── Summary Stats ────────────────────────────────────────────────────────────

def get_meta_stats() -> dict:
    """Return summary stats for meta-labeling readiness."""
    stats = get_stats()
    return {
        "db_predictions": stats["total_predictions"],
        "db_signals":     stats["total_signals"],
        "db_open":        stats["open_signals"],
        "db_closed":      stats["closed_signals"],
        "db_outcomes":    stats["total_outcomes"],
        "ready":          stats["closed_signals"] >= 20,  # minimum viable sample
        "dir_acc_threshold_confirm":  DIR_ACC_CONFIRM_THRESHOLD,
        "dir_acc_threshold_reverse":  DIR_ACC_REVERSE_THRESHOLD,
        "mae_threshold_confirm":      MAE_CONFIRM_THRESHOLD,
        "confidence_override":        CONFIDENCE_OVERRIDE_THRESHOLD,
    }
