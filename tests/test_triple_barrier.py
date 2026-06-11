import numpy as np
import pandas as pd
import pytest

from self_learn.triple_barrier import LABEL_COLUMNS, label_triple_barrier

TP = 0.02
SL = 0.02
MAX_BARS = 30


def _flat(n: int, price: float = 100.0) -> pd.Series:
    return pd.Series(np.full(n, price))


def test_output_shape_and_columns():
    out = label_triple_barrier(_flat(50), TP, SL, MAX_BARS)
    assert list(out.columns) == LABEL_COLUMNS
    assert len(out) == 50


def test_tp_hit_first():
    close = _flat(50)
    close.iloc[5] = 103.0  # +3% at bar 5 — TP for entries near bar 0
    out = label_triple_barrier(close, TP, SL, MAX_BARS)
    assert out.loc[0, "hit_tp_first"] == 1.0
    assert out.loc[0, "bars_to_exit"] == 5.0
    assert out.loc[0, "ret_h"] == pytest.approx(TP)


def test_sl_hit_first():
    close = _flat(50)
    close.iloc[3] = 97.0  # -3% at bar 3
    out = label_triple_barrier(close, TP, SL, MAX_BARS)
    assert out.loc[0, "hit_tp_first"] == 0.0
    assert out.loc[0, "bars_to_exit"] == 3.0
    assert out.loc[0, "ret_h"] == pytest.approx(-SL)


def test_timeout_uses_vertical_barrier_close():
    n = 80
    drift = np.linspace(100.0, 100.5, n)  # +0.5% over 80 bars — never touches barriers
    out = label_triple_barrier(pd.Series(drift), TP, SL, MAX_BARS)
    assert out.loc[0, "hit_tp_first"] == 0.0
    assert out.loc[0, "bars_to_exit"] == float(MAX_BARS)
    expected = drift[MAX_BARS] / drift[0] - 1.0
    assert out.loc[0, "ret_h"] == pytest.approx(expected)


def test_tail_rows_without_full_horizon_are_invalid():
    out = label_triple_barrier(_flat(40), TP, SL, MAX_BARS)
    # Rows 0..9 have 30 forward bars; rows 10+ do not and never resolve.
    assert out.loc[9, "valid"]
    assert not out.loc[10, "valid"]
    assert np.isnan(out.loc[10, "ret_h"])


def test_tail_row_resolved_by_barrier_stays_valid():
    close = _flat(40)
    close.iloc[38] = 103.0  # row 35 hits TP at offset 3 despite short horizon
    out = label_triple_barrier(close, TP, SL, MAX_BARS)
    assert out.loc[35, "valid"]
    assert out.loc[35, "hit_tp_first"] == 1.0


def test_same_bar_double_touch_is_conservative_sl():
    close = _flat(50)
    high = close.copy()
    low = close.copy()
    high.iloc[4] = 103.0  # both barriers pierced inside bar 4
    low.iloc[4] = 97.0
    out = label_triple_barrier(close, TP, SL, MAX_BARS, high=high, low=low)
    assert out.loc[0, "hit_tp_first"] == 0.0
    assert out.loc[0, "ret_h"] == pytest.approx(-SL)


def test_labels_do_not_cross_session_boundary():
    # Two sessions of 35 bars each. A huge spike right after the boundary
    # must not be visible to entries in session 1.
    close = _flat(70)
    close.iloc[36] = 110.0  # second bar of session 2
    sess = pd.Series([1] * 35 + [2] * 35)
    out = label_triple_barrier(close, TP, SL, MAX_BARS, session_id=sess)
    # Row 34 is last bar of session 1: no forward bars in-session → invalid.
    assert not out.loc[34, "valid"]
    # Row 30 has only 4 in-session forward bars, none touch a barrier → invalid.
    assert not out.loc[30, "valid"]
    # Row 4 of session 1 has full horizon in-session, flat → timeout.
    assert out.loc[4, "valid"]
    assert out.loc[4, "hit_tp_first"] == 0.0
    # Session 2 entry at row 35 sees the spike at row 36.
    assert out.loc[35, "hit_tp_first"] == 1.0


def test_invalid_params_raise():
    with pytest.raises(ValueError):
        label_triple_barrier(_flat(10), 0.0, SL, MAX_BARS)
    with pytest.raises(ValueError):
        label_triple_barrier(_flat(10), TP, -0.01, MAX_BARS)
    with pytest.raises(ValueError):
        label_triple_barrier(_flat(10), TP, SL, 0)
