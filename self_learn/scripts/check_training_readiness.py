#!/usr/bin/env python3
"""
Training Readiness Check — meta_011 Prep Script
=================================================
Reports whether the meta-labeling training pipeline is ready to run.

Reads from self_learn DB:
  - closed_signals (need ≥20 for meta-model training)
  - outcomes with prediction_error (need ≥20 for reliable MAE/directional_accuracy)
  - model versions trained so far

No live trading impact — read-only analysis script.

Usage:
    cd kiro-quant-v3
    PYTHONPATH=. python3 self_learn/scripts/check_training_readiness.py
"""

from __future__ import annotations
import sys
import json
from datetime import datetime, timezone

# ── Add project root to path ──────────────────────────────────────────────
sys.path.insert(0, "/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3")

from self_learn.models import get_stats, get_prediction_accuracy
from self_learn.config import RETRAIN_MIN_OUTCOMES


# ── Thresholds ─────────────────────────────────────────────────────────────
MIN_CLOSED_FOR_META_TRAINING = max(RETRAIN_MIN_OUTCOMES, 20)  # at least 20
MIN_SYMBOLS_WITH_HISTORY = 3                                     # need ≥3 symbols


def check_training_readiness() -> dict:
    """Return a readiness report dict."""
    stats = get_stats()

    closed = stats["closed_signals"]
    outcomes = stats["total_outcomes"]
    open_signals = stats["open_signals"]
    predictions = stats["total_predictions"]

    # ── Outcome quality check ──────────────────────────────────────────────
    # We need outcomes where prediction_error is not null (otherwise MAE=0.5 default)
    # Quick sample: check a few well-known symbols
    sample_symbols = ["9988.HK", "0700.HK", "AAPL", "NVDA", "TSLA"]
    symbols_with_data = 0
    for sym in sample_symbols:
        mae, dir_acc = get_prediction_accuracy(sym, window=20)
        if dir_acc != 0.5 or mae != 0.0:  # not the default "no data" return
            symbols_with_data += 1

    # ── Compute readiness ────────────────────────────────────────────────
    closed_ok = closed >= MIN_CLOSED_FOR_META_TRAINING
    outcomes_ok = outcomes >= MIN_CLOSED_FOR_META_TRAINING
    symbols_ok = symbols_with_data >= MIN_SYMBOLS_WITH_HISTORY

    ready = closed_ok and outcomes_ok and symbols_ok

    # ── Per-symbol breakdown (top 5 by prediction count) ─────────────────
    # We don't have a get_all_symbols helper, so we derive from recent outcomes
    # Just report summary stats
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "min_closed_signals": MIN_CLOSED_FOR_META_TRAINING,
            "min_outcomes": MIN_CLOSED_FOR_META_TRAINING,
            "min_symbols_with_history": MIN_SYMBOLS_WITH_HISTORY,
        },
        "db_state": {
            "predictions": predictions,
            "total_signals": stats["total_signals"],
            "open_signals": open_signals,
            "closed_signals": closed,
            "total_outcomes": outcomes,
            "total_pnl": stats["total_pnl"],
            "avg_pnl_pct": stats["avg_pnl_pct"],
        },
        "quality_checks": {
            "closed_signals_ok": closed_ok,
            "outcomes_ok": outcomes_ok,
            "symbols_with_history": symbols_with_data,
            "symbols_ok": symbols_ok,
        },
        "model_state": stats.get("current_model_version"),
        "readiness": {
            "ready": ready,
            "blocked_by": _blocked_by(closed_ok, outcomes_ok, symbols_ok),
        },
    }
    return report


def _blocked_by(closed_ok: bool, outcomes_ok: bool, symbols_ok: bool) -> list[str]:
    reasons = []
    if not closed_ok:
        reasons.append(f"closed_signals < {MIN_CLOSED_FOR_META_TRAINING}")
    if not outcomes_ok:
        reasons.append(f"outcomes < {MIN_CLOSED_FOR_META_TRAINING}")
    if not symbols_ok:
        reasons.append(f"symbols_with_history < {MIN_SYMBOLS_WITH_HISTORY}")
    return reasons


def main():
    report = check_training_readiness()
    print(json.dumps(report, indent=2, default=str))
    
    # ── CLI exit code ────────────────────────────────────────────────────
    if report["readiness"]["ready"]:
        print("\n✅ READY — meta-model training can proceed.")
        print("   Next: python3 -m self_learn.retrain retrain")
        sys.exit(0)
    else:
        blockers = report["readiness"]["blocked_by"]
        print(f"\n❌ NOT READY — blocked by: {', '.join(blockers)}")
        print(f"   Current: closed={report['db_state']['closed_signals']}, "
              f"outcomes={report['db_state']['total_outcomes']}, "
              f"symbols={report['quality_checks']['symbols_with_history']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
