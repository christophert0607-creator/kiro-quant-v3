#!/usr/bin/env python3
"""
Evaluate Open Signals — Meta-Labeling Audit Script (meta_007)

Purpose: Batch audit all currently OPEN signals using meta_labeler.should_take_trade()
to see which would be confirmed/rejected/reversed based on prediction accuracy history.

This is a READ-ONLY diagnostic script:
- Does NOT touch live trading
- Does NOT modify DB
- Does NOT affect risk management
- ONLY reads and reports meta-labeling decisions for existing signals

Usage:
    python3 self_learn/scripts/evaluate_open_signals.py

Output:
    Table of open signals with meta-labeling decisions and reasons
    Summary stats: total OPEN, confirmed count, rejected count, etc.

Requires:
    - self_learn.meta_labeler (for decisions)
    - self_learn.models (for open signals)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root in path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from self_learn.models import get_session, Signal, Prediction
from self_learn.meta_labeler import (
    should_take_trade,
    Decision,
    get_meta_stats,
)


def format_decision(decision: Decision) -> str:
    """Format decision with emoji."""
    icons = {
        Decision.CONFIRM: "✅ CONFIRM",
        Decision.REJECT: "❌ REJECT",
        Decision.REVERSE: "🔄 REVERSE",
        Decision.NO_DATA: "⚠️  NO_DATA",
    }
    return icons.get(decision, str(decision))


def main():
    print("=" * 70)
    print("META-LABELING AUDIT: Evaluate Open Signals")
    print("=" * 70)
    
    # Get meta stats first
    stats = get_meta_stats()
    print(f"\n📊 Database Summary:")
    print(f"   Predictions: {stats['db_predictions']:,}")
    print(f"   Signals (total): {stats['db_signals']}")
    print(f"   Signals (OPEN): {stats['db_open']}")
    print(f"   Signals (CLOSED): {stats['db_closed']}")
    print(f"   Outcomes: {stats['db_outcomes']}")
    print(f"   Ready for training: {stats.get('ready', False)}")
    
    print(f"\n⚙️  Thresholds:")
    print(f"   DIR_ACC_CONFIRM: {stats.get('dir_acc_threshold_confirm', 0.55)}")
    print(f"   DIR_ACC_REVERSE: {stats.get('dir_acc_threshold_reverse', 0.40)}")
    print(f"   MAE_CONFIRM: {stats.get('mae_threshold_confirm', 5.0)}")
    print(f"   CONFIDENCE_OVERRIDE: {stats.get('confidence_override', 0.80)}")
    
    # Get open signals with their predictions joined (avoid N+1)
    with get_session() as sess:
        stmt = (
            select(Signal, Prediction)
            .outerjoin(Prediction, Signal.prediction_id == Prediction.id)
            .where(Signal.status == "OPEN")
            .limit(100)  # Sample first 100 for readability
        )
        results = sess.execute(stmt).all()
    
    if not results:
        print("\n📭 No OPEN signals found in database.")
        print("   This is expected if no trades are currently active.")
        return 0
    
    print(f"\n📋 Evaluating {len(results)} OPEN signal(s) (sample)...\n")
    print("-" * 70)
    print(f"{'Signal ID':<38} {'Action':<6} {'Decision':<14} {'Confidence':>10} {'Reason'}")
    print("-" * 70)
    
    # Track summary counts
    counts = {
        Decision.CONFIRM: 0,
        Decision.REJECT: 0,
        Decision.REVERSE: 0,
        Decision.NO_DATA: 0,
    }
    
    # Evaluate each open signal
    for row in results:
        sig = row[0]
        pred = row[1] if len(row) > 1 else None
        
        # Extract prediction data
        predicted_price = pred.predicted_price if pred else None
        confidence = pred.confidence if pred else None
        symbol = pred.symbol if pred else "UNKNOWN"
        
        # Use entry_price from signal
        entry_price = sig.entry_price or 0.0
        
        # Evaluate with meta-labeler
        meta = should_take_trade(
            symbol=symbol,
            action=sig.action,
            entry_price=entry_price,
            predicted_price=predicted_price or 0.0,
            confidence=confidence or 0.5,
        )
        
        # Count decisions
        counts[meta.decision] = counts.get(meta.decision, 0) + 1
        
        # Format output
        sig_id_short = sig.id[:8] + "..." if len(sig.id) > 8 else sig.id
        decision_str = format_decision(meta.decision)
        reason = meta.reason[:35] + "..." if len(meta.reason) > 35 else meta.reason
        
        print(f"{sig_id_short:<38} {sig.action:<6} {decision_str:<14} {meta.confidence:>9.0%} {reason}")
    
    print("-" * 70)
    
    # Summary
    print(f"\n📈 Decision Summary:")
    for dec, cnt in counts.items():
        pct = cnt / len(results) * 100 if results else 0
        print(f"   {format_decision(dec):<14}: {cnt:>3} ({pct:>5.1f}%)")
    
    total = len(results)
    actionable = counts[Decision.CONFIRM] + counts[Decision.REVERSE]
    actionable_pct = actionable / total * 100 if total else 0
    
    print(f"\n💡 Insight:")
    if actionable == 0:
        print(f"   No actionable signals — all would be skipped or lack data.")
        print(f"   Awaiting live closed outcomes for training (need ≥20).")
    else:
        print(f"   {actionable}/{total} signals ({actionable_pct:.1f}%) have actionable meta decisions.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())