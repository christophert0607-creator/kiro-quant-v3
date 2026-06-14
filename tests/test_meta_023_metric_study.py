from dev.meta_labeling.scripts.meta_023_metric_study import (
    OutcomeRow,
    decision_from_dir_acc,
    score_outcomes,
    summarize,
)


def row(signal_id, symbol, predicted_price, exit_price, pnl_pct):
    return OutcomeRow(
        signal_id=signal_id,
        symbol=symbol,
        action="BUY",
        entry_price=100.0,
        predicted_price=predicted_price,
        exit_price=exit_price,
        pnl_pct=pnl_pct,
        source="synthetic_seed",
    )


def test_decision_from_dir_acc_thresholds_and_no_data():
    assert decision_from_dir_acc(None, 0.55, 0.40) == "NO_DATA"
    assert decision_from_dir_acc(0.70, 0.55, 0.40) == "CONFIRM"
    assert decision_from_dir_acc(0.20, 0.55, 0.40) == "REVERSE"
    assert decision_from_dir_acc(0.50, 0.55, 0.40) == "NO_DATA"


def test_score_outcomes_leave_one_out_and_summary_metrics():
    rows = [
        row("a1", "AAA", 105.0, 103.0, 3.0),  # correct peer for AAA
        row("a2", "AAA", 105.0, 102.0, 2.0),  # correct peer for AAA
        row("b1", "BBB", 95.0, 102.0, 2.0),   # wrong peer for BBB
        row("b2", "BBB", 95.0, 101.0, 1.0),   # wrong peer for BBB
        row("c1", "CCC", 105.0, 101.0, 1.0),  # no peer -> NO_DATA
    ]

    scored = score_outcomes(rows, min_history=1, confirm_threshold=0.55, reverse_threshold=0.40)
    decisions = {item.row.signal_id: item.decision for item in scored}

    assert decisions["a1"] == "CONFIRM"
    assert decisions["a2"] == "CONFIRM"
    assert decisions["b1"] == "REVERSE"
    assert decisions["b2"] == "REVERSE"
    assert decisions["c1"] == "NO_DATA"

    report = summarize(scored)
    assert report["total_outcomes"] == 5
    assert report["judged_outcomes"] == 4
    assert report["no_data_outcomes"] == 1
    assert report["coverage"] == 0.8
    assert report["covered_accuracy"] == 0.5  # two CONFIRM winners correct, two REVERSE winners wrong
    assert report["weighted_accuracy_by_abs_pnl"] == 0.625  # (3 + 2) / (3 + 2 + 2 + 1)
    assert report["pnl_no_meta_pct_points"] == 9.0
    assert report["pnl_with_meta_pct_points"] == 3.0  # 3 + 2 - 2 - 1 + 1
    assert report["pnl_delta_pct_points"] == -6.0
