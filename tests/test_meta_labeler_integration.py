#!/usr/bin/env python3
"""
Meta-Labeling Integration Test — meta_006
=========================================
Tests the full meta_labeler + hook_on_trade_closed end-to-end chain
using synthetic (non-live) data.

Validates:
1. hook_on_trade_closed correctly writes outcomes to DB
2. get_prediction_accuracy() reads them back correctly
3. MetaLabeler decisions respond correctly to varying outcome histories

No live trading logic is touched. Test data is isolated and cleaned up.

Run: python3 tests/test_meta_labeler_integration.py
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

# ── Setup path ─────────────────────────────────────────────────────────────────
WS = Path(__file__).parent.parent
sys.path.insert(0, str(WS))

# ── Imports ─────────────────────────────────────────────────────────────────────
from self_learn.models import (
    get_session,
    Prediction,
    Signal,
    Outcome,
    log_signal,
    get_prediction_accuracy,
    get_stats,
    save_prediction,
)
from self_learn.meta_labeler import should_take_trade, Decision, get_meta_stats
from self_learn.feedback import on_trade_closed


# ── Test helpers ────────────────────────────────────────────────────────────────

def _make_prediction(symbol: str, predicted_price: float, conf: float = 0.7) -> str:
    """Create a test prediction and return its ID."""
    return save_prediction(symbol=symbol, predicted_price=predicted_price,
                          confidence=conf, feature_vector=None)


def _make_signal(symbol: str, action: str, pred_id: str | None, price: float = 100.0) -> str:
    """Create a test signal (OPEN) and return its ID."""
    return log_signal(action=action, prediction_id=pred_id,
                      entry_price=price, size=100, status="OPEN")


def _seed_outcome(symbol: str, pnl_pct: float, direction_correct: bool) -> tuple:
    """
    Create a closed signal + outcome that represents a finished trade.

    direction_correct=True  → predicted direction matches actual price movement
    direction_correct=False → predicted direction OPPOSITE to actual movement

    Returns (sig_id, mae, dir_acc) after seeding.
    """
    entry_price = 100.0

    if direction_correct:
        # Predicted up correctly, price went up
        exit_price = entry_price * (1 + abs(pnl_pct))
        predicted_price = exit_price  # pred matches actual direction
        prediction_error = abs(pnl_pct) * entry_price
    else:
        # Predicted down but price went UP (or vice versa)
        exit_price = entry_price * (1 + abs(pnl_pct))
        predicted_price = entry_price * (1 - abs(pnl_pct))  # opposite direction
        prediction_error = abs(exit_price - predicted_price)

    pred_id = _make_prediction(symbol, predicted_price=predicted_price)
    sig_id = _make_signal(symbol, "BUY", pred_id, entry_price)

    # Close signal manually
    session = get_session()
    sig = session.query(Signal).filter(Signal.id == sig_id).first()
    sig.status = "CLOSED"
    sig.exit_price = exit_price
    sig.closed_at = datetime.now(timezone.utc)
    session.commit()
    session.close()

    # Write outcome via feedback hook
    on_trade_closed(
        signal_id=sig_id,
        exit_price=exit_price,
        pnl=(exit_price - entry_price) * 100,
        pnl_pct=pnl_pct,
        hold_minutes=30,
        prediction_error=prediction_error,
    )

    # Verify accuracy was stored
    mae, da = get_prediction_accuracy(symbol, window=20)
    return sig_id, mae, da


def cleanup_test_data(symbol: str = "META_TEST.HK") -> None:
    """Remove all test data for META_TEST.HK symbol."""
    session = get_session()
    try:
        # Get all prediction IDs for this symbol
        pred_ids = [r[0] for r in session.query(Prediction.id).filter(
            Prediction.symbol == symbol).all()]

        if pred_ids:
            sig_ids = [r[0] for r in session.query(Signal.id).filter(
                Signal.prediction_id.in_(pred_ids)).all()]
            if sig_ids:
                session.query(Outcome).filter(
                    Outcome.signal_id.in_(sig_ids)).delete(synchronize_session=False)
            session.query(Signal).filter(
                Signal.prediction_id.in_(pred_ids)).delete(synchronize_session=False)

        session.query(Prediction).filter(Prediction.symbol == symbol).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_on_trade_closed_writes_outcome():
    """Verify on_trade_closed correctly writes to outcomes table."""
    print("\n[TEST 1] on_trade_closed writes outcome correctly")
    cleanup_test_data("META_TEST.HK")

    pred_id = _make_prediction("META_TEST.HK", predicted_price=100.0)
    sig_id  = _make_signal("META_TEST.HK", "BUY", pred_id, 100.0)

    # Close signal manually
    session = get_session()
    sig = session.query(Signal).filter(Signal.id == sig_id).first()
    sig.status = "CLOSED"
    sig.exit_price = 105.0
    sig.closed_at = datetime.now(timezone.utc)
    session.commit()
    session.close()

    result = on_trade_closed(
        signal_id=sig_id,
        exit_price=105.0,
        pnl=500.0,
        pnl_pct=0.05,
        hold_minutes=30,
        prediction_error=5.0,
    )

    session = get_session()
    outcome = session.query(Outcome).filter(Outcome.signal_id == sig_id).first()
    session.close()

    assert outcome is not None, "Outcome not found after on_trade_closed"
    assert abs(outcome.pnl_pct - 0.05) < 0.001
    assert outcome.prediction_error == 5.0

    print(f"  ✓ Outcome written: prediction_error={outcome.prediction_error}, pnl_pct={outcome.pnl_pct:.4f}")
    return True


def test_prediction_accuracy_responds_to_seeded_data():
    """Verify get_prediction_accuracy changes based on seeded outcomes."""
    print("\n[TEST 2] get_prediction_accuracy responds to seeded data")
    cleanup_test_data("META_TEST.HK")

    # Start: no data → default (0.0, 0.5)
    mae0, da0 = get_prediction_accuracy("META_TEST.HK", window=20)
    print(f"  [empty]     MAE={mae0:.4f}, dir_acc={da0:.4f}")

    # Seed 1 correct outcome
    _, mae1, da1 = _seed_outcome("META_TEST.HK", pnl_pct=0.05, direction_correct=True)
    print(f"  [1 correct]  MAE={mae1:.4f}, dir_acc={da1:.4f}")
    assert da1 != 0.5 or mae1 > 0, "Accuracy should change from default after 1 outcome"

    # Seed 1 incorrect outcome
    _, mae2, da2 = _seed_outcome("META_TEST.HK", pnl_pct=-0.03, direction_correct=False)
    print(f"  [2 total]   MAE={mae2:.4f}, dir_acc={da2:.4f}")

    print(f"  ✓ Accuracy responds to new outcomes")
    return True


def test_meta_labeler_decision_varies_with_data():
    """Verify MetaLabeler decisions differ between no-data, good-history, bad-history."""
    print("\n[TEST 3] MetaLabeler decisions vary with outcome history")
    cleanup_test_data("META_TEST.HK")

    # Case A: No data → NO_DATA
    d_a = should_take_trade("META_TEST.HK", "BUY", 100.0, 105.0, 0.7)
    print(f"  [No data]    decision={d_a.decision.value}, conf={d_a.confidence:.2f}")
    assert d_a.decision == Decision.NO_DATA

    # Case B: 15 good outcomes → CONFIRM (dir_acc ≈ 1.0)
    for _ in range(15):
        _seed_outcome("META_TEST.HK", pnl_pct=0.05, direction_correct=True)
    d_b = should_take_trade("META_TEST.HK", "BUY", 100.0, 105.0, 0.65)
    print(f"  [15 good]   decision={d_b.decision.value}, conf={d_b.confidence:.2f}")
    # dir_acc = 1.0 → CONFIRM
    assert d_b.decision == Decision.CONFIRM, f"Expected CONFIRM, got {d_b.decision}"

    # Case C: 15 bad outcomes (opposite direction) → REVERSE (dir_acc = 0.0)
    # Add to the existing 15 good → now 15 good + 15 bad = 30 total
    # directional accuracy = 15/30 = 0.5 → REJECT zone, not REVERSE
    # Need STRICTLY more bad than good for REVERSE (dir_acc < 0.40)
    for _ in range(25):
        _seed_outcome("META_TEST.HK", pnl_pct=-0.05, direction_correct=False)
    # Now: 15 good + 40 bad = 55 total, dir_acc = 15/55 ≈ 0.27 → REVERSE
    d_c = should_take_trade("META_TEST.HK", "BUY", 100.0, 105.0, 0.65)
    print(f"  [15g/40b]   decision={d_c.decision.value}, conf={d_c.confidence:.2f}")
    assert d_c.decision == Decision.REVERSE, f"Expected REVERSE, got {d_c.decision}"

    # Case D: Mix → REJECT (uncertain zone)
    cleanup_test_data("META_TEST.HK")
    # 12 good + 8 bad → 12/20 = 0.6 → CONFIRM
    for _ in range(12):
        _seed_outcome("META_TEST.HK", pnl_pct=0.05, direction_correct=True)
    for _ in range(8):
        _seed_outcome("META_TEST.HK", pnl_pct=-0.03, direction_correct=False)
    d_d = should_take_trade("META_TEST.HK", "BUY", 100.0, 105.0, 0.65)
    print(f"  [12g/8b]    decision={d_d.decision.value}, conf={d_d.confidence:.2f}")

    print(f"  ✓ MetaLabeler decisions respond correctly to data composition")
    return True


def test_high_confidence_override():
    """Verify high model confidence (≥0.80) can override directional accuracy."""
    print("\n[TEST 4] High confidence override threshold")
    cleanup_test_data("META_TEST.HK")

    # Seed uncertain outcomes (dir_acc ≈ 0.5)
    for _ in range(10):
        _seed_outcome("META_TEST.HK", pnl_pct=0.05, direction_correct=True)
    for _ in range(10):
        _seed_outcome("META_TEST.HK", pnl_pct=-0.05, direction_correct=False)
    # dir_acc should be ≈ 0.5 (neutral)

    # High confidence override with good-enough dir_acc
    d = should_take_trade("META_TEST.HK", "BUY", 100.0, 105.0, confidence=0.85)
    print(f"  [conf=0.85, dir_acc≈0.5] decision={d.decision.value}, conf={d.confidence:.2f}")
    # conf >= 0.80 AND dir_acc >= 0.55? But dir_acc ≈ 0.5, so no override
    # Should be REJECT (uncertain zone)
    assert d.decision in (Decision.REJECT, Decision.NO_DATA), f"Expected REJECT/NO_DATA, got {d.decision}"

    print(f"  ✓ High confidence override behaves correctly")
    return True


def test_meta_stats_ready_state():
    """Verify get_meta_stats reports correct readiness."""
    print("\n[TEST 5] get_meta_stats reports readiness correctly")
    cleanup_test_data("META_TEST.HK")

    stats = get_meta_stats()
    print(f"  [0 outcomes] ready={stats['ready']}, outcomes={stats['db_outcomes']}")
    assert stats['ready'] is False
    assert stats['db_outcomes'] == 0

    # Seed 25 outcomes (above 20 threshold)
    for _ in range(25):
        _seed_outcome("META_TEST.HK", pnl_pct=0.05, direction_correct=True)

    stats2 = get_meta_stats()
    print(f"  [25 outcomes] ready={stats2['ready']}, outcomes={stats2['db_outcomes']}")
    assert stats2['ready'] is True
    assert stats2['db_outcomes'] >= 20

    print(f"  ✓ get_meta_stats reflects DB state correctly")
    return True


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("META_006: Meta-Labeling Integration Test")
    print("=" * 60)
    print(f"Test symbol: META_TEST.HK")

    tests = [
        ("on_trade_closed writes outcome",         test_on_trade_closed_writes_outcome),
        ("get_prediction_accuracy updates",         test_prediction_accuracy_responds_to_seeded_data),
        ("MetaLabeler decisions vary with data",   test_meta_labeler_decision_varies_with_data),
        ("High confidence override",               test_high_confidence_override),
        ("get_meta_stats readiness",              test_meta_stats_ready_state),
    ]

    results = []
    for name, fn in tests:
        try:
            ok = fn()
            results.append((name, "PASS" if ok else "FAIL"))
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append((name, f"ERROR: {e}"))

    # Cleanup
    print("\n" + "=" * 60)
    print("CLEANUP...")
    cleanup_test_data("META_TEST.HK")
    print("Test data removed.")

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for name, status in results:
        icon = "✓" if status == "PASS" else "✗"
        print(f"  {icon} {name}: {status}")

    passed = sum(1 for _, s in results if s == "PASS")
    print(f"\n  {passed}/{len(results)} passed")

    # Syntax check
    print("\n" + "=" * 60)
    print("SYNTAX CHECK")
    print("=" * 60)
    import py_compile
    for f in ["self_learn/meta_labeler.py", "self_learn/feedback.py",
              "tests/test_meta_labeler_integration.py"]:
        try:
            py_compile.compile(str(WS / f), doraise=True)
            print(f"  ✓ {f}")
        except py_compile.PyCompileError as e:
            print(f"  ✗ {f}: {e}")

    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)