"""
MDS (Metric Dependence Screening) pre-filter for symbol universe.

§9.5 research guide: pre-screen candidates by Fréchet variation score
computed from daily returns (scalar) + intraday risk curve (functional proxy).
Lower score = more consistent intraday pattern = higher quality candidate.

Trigger conditions (§9.5):
  - Candidate pool > 50 symbols
  - Regime = Risk-Off
  - HK small-cap / low-liquidity market

Usage:
  scores = score_symbols(market_buffers)
  filtered = filter_universe(scores, top_pct=0.20, min_symbols=10)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    pass

_MDS_SCORES_PATH = Path(__file__).parent.parent.parent / "data" / "mds_scores.json"
_MIN_BARS = 20
_TOP_PCT_DEFAULT = 0.20
_MIN_SYMBOLS_DEFAULT = 10


def _frechet_variation_score(df: pd.DataFrame) -> float:
    """Compute simplified Fréchet variation score for one symbol.

    Uses coefficient of variation of the intraday range percentage as a
    functional-data proxy for the intraday risk curve (§9.5).

    Lower score = more consistent intraday pattern = better candidate.
    Returns float('inf') when data is insufficient.
    """
    if df is None or len(df) < _MIN_BARS:
        return float("inf")

    try:
        high = df["High"].values.astype(float)
        low = df["Low"].values.astype(float)
        close = df["Close"].values.astype(float)

        # Intraday range as fraction of close — proxy for daily risk curve
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        range_pct = (high - low) / np.where(prev_close > 0, prev_close, 1.0)
        mean_range = float(np.nanmean(range_pct))
        if mean_range <= 0:
            return float("inf")
        cv_range = float(np.nanstd(range_pct)) / mean_range  # coefficient of variation

        # Daily return volatility — penalise highly erratic symbols
        returns = np.diff(close) / np.where(close[:-1] > 0, close[:-1], 1.0)
        vol_return = float(np.nanstd(returns))

        # Combined score: lower = more predictable
        return cv_range * (1.0 + vol_return * 10.0)
    except Exception:
        return float("inf")


def score_symbols(market_buffers: dict[str, pd.DataFrame]) -> dict[str, float]:
    """Return a {symbol: mds_score} dict for all symbols with enough data.

    Lower MDS score = more consistent intraday pattern.
    """
    scores: dict[str, float] = {}
    for sym, df in market_buffers.items():
        scores[sym] = _frechet_variation_score(df)
    return scores


def filter_universe(
    scores: dict[str, float],
    top_pct: float = _TOP_PCT_DEFAULT,
    min_symbols: int = _MIN_SYMBOLS_DEFAULT,
) -> list[str]:
    """Return symbols with the lowest (best) MDS scores.

    Keeps top `top_pct` fraction (default 20%), floored at `min_symbols`.
    Symbols with infinite score (insufficient data) are always excluded from
    the filtered set but not from the fallback — caller must handle that.
    """
    finite = {s: v for s, v in scores.items() if v != float("inf")}
    if not finite:
        return list(scores.keys())

    sorted_syms = sorted(finite, key=lambda s: finite[s])
    n_keep = max(min_symbols, int(len(sorted_syms) * top_pct))
    n_keep = min(n_keep, len(sorted_syms))
    return sorted_syms[:n_keep]


def save_scores(scores: dict[str, float], path: Path = _MDS_SCORES_PATH) -> None:
    """Persist MDS scores to data/mds_scores.json for inspection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialisable = {s: (v if v != float("inf") else None) for s, v in scores.items()}
    sorted_entry = dict(sorted(serialisable.items(), key=lambda x: (x[1] is None, x[1] or 0)))
    path.write_text(json.dumps(sorted_entry, indent=2))


def load_scores(path: Path = _MDS_SCORES_PATH) -> dict[str, float]:
    """Load previously saved MDS scores; returns {} if file absent."""
    try:
        raw = json.loads(path.read_text())
        return {s: float("inf") if v is None else float(v) for s, v in raw.items()}
    except Exception:
        return {}
