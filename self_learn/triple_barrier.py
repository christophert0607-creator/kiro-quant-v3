"""Triple-barrier labeling for HKAlpha-1 training.

Labels each bar with the outcome a live position opened at that bar's close
would have seen, using the same barriers the live loop enforces:

    upper barrier   = +tp        (config quick_take_profit_pct)
    lower barrier   = -sl        (config stop_loss_pct)
    vertical barrier = max_bars  (config max_hold_bars)

Labels are session-aware: the barrier search never crosses a session boundary
(a trading date), because an overnight gap inside a label would teach the
model about a holding period the live loop never has. Bars whose session has
fewer than ``max_bars`` forward bars are marked invalid rather than silently
labeled with a shorter horizon.

If both barriers are touched within the same bar (only detectable when
high/low are provided), the stop-loss is assumed to fire first — the
conservative assumption.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LABEL_COLUMNS = ["ret_h", "hit_tp_first", "bars_to_exit", "valid"]


def label_triple_barrier(
    close: pd.Series,
    tp: float,
    sl: float,
    max_bars: int,
    *,
    high: pd.Series | None = None,
    low: pd.Series | None = None,
    session_id: pd.Series | None = None,
) -> pd.DataFrame:
    """Return a DataFrame aligned to ``close`` with columns LABEL_COLUMNS.

    ret_h:         simple return entry→exit (exit = barrier touch or vertical)
    hit_tp_first:  1.0 if TP touched before SL, 0.0 otherwise (incl. timeout)
    bars_to_exit:  bars held until exit
    valid:         False where the session lacks max_bars of forward bars
    """
    if tp <= 0 or sl <= 0:
        raise ValueError(f"tp and sl must be positive fractions, got tp={tp} sl={sl}")
    if max_bars < 1:
        raise ValueError(f"max_bars must be >= 1, got {max_bars}")

    c = pd.to_numeric(close, errors="coerce").astype(float).to_numpy()
    n = len(c)
    h = pd.to_numeric(high, errors="coerce").astype(float).to_numpy() if high is not None else c
    l = pd.to_numeric(low, errors="coerce").astype(float).to_numpy() if low is not None else c
    if len(h) != n or len(l) != n:
        raise ValueError("high/low must align with close")

    if session_id is not None:
        sess = pd.Series(session_id).to_numpy()
        if len(sess) != n:
            raise ValueError("session_id must align with close")
    else:
        sess = np.zeros(n)

    entry = c
    upper = entry * (1.0 + tp)
    lower = entry * (1.0 - sl)

    exit_offset = np.full(n, max_bars, dtype=int)   # vertical barrier default
    hit_tp = np.zeros(n, dtype=float)
    resolved = np.zeros(n, dtype=bool)

    # Vectorized scan over forward offsets: 30 passes over the array instead
    # of a Python loop per row.
    for k in range(1, max_bars + 1):
        fwd_high = np.roll(h, -k)
        fwd_low = np.roll(l, -k)
        fwd_sess = np.roll(sess, -k)
        in_range = np.arange(n) + k < n
        same_session = in_range & (fwd_sess == sess)

        tp_touch = same_session & (fwd_high >= upper)
        sl_touch = same_session & (fwd_low <= lower)

        # Same-bar double touch resolves to SL (conservative).
        newly_sl = ~resolved & sl_touch
        newly_tp = ~resolved & tp_touch & ~sl_touch

        exit_offset[newly_sl | newly_tp] = k
        hit_tp[newly_tp] = 1.0
        resolved |= newly_sl | newly_tp

    # Validity: every offset 1..max_bars must exist within the same session,
    # unless a barrier resolved the label earlier than the missing bars.
    last_offset = np.full(n, max_bars, dtype=int)
    idx = np.arange(n)
    for k in range(1, max_bars + 1):
        fwd_sess = np.roll(sess, -k)
        available = (idx + k < n) & (fwd_sess == sess)
        # First missing forward bar caps the usable horizon at k-1.
        cap = ~available & (last_offset == max_bars)
        last_offset[cap] = k - 1

    valid = resolved | (last_offset >= max_bars)
    # For unresolved-but-valid rows the vertical barrier is the exit.
    exit_offset = np.where(resolved, exit_offset, max_bars)

    exit_idx = np.clip(idx + exit_offset, 0, n - 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret_h = c[exit_idx] / entry - 1.0
    # Barrier-touch exits realize the barrier return, not the bar close —
    # matches what a live limit/stop fill would deliver.
    touched_tp = resolved & (hit_tp == 1.0)
    touched_sl = resolved & (hit_tp == 0.0)
    ret_h = np.where(touched_tp, tp, ret_h)
    ret_h = np.where(touched_sl, -sl, ret_h)
    ret_h = np.where(np.isfinite(ret_h), ret_h, 0.0)

    out = pd.DataFrame(
        {
            "ret_h": ret_h,
            "hit_tp_first": hit_tp,
            "bars_to_exit": exit_offset.astype(float),
            "valid": valid,
        },
        index=close.index if isinstance(close, pd.Series) else None,
    )
    out.loc[~out["valid"], ["ret_h", "hit_tp_first", "bars_to_exit"]] = np.nan
    return out
