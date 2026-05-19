"""
Causal Factor Filter — §9.8 研究指南

Uses Instrumental Variable (IV) regression to screen out spurious momentum factors.
IV = Fed Funds Rate proxy (^IRX, 13-week T-bill) — influences liquidity but is
independent of market sentiment, making it a valid instrument.

Principle: If a factor's predictive correlation with returns drops significantly
after removing IV's influence (partial_corr < 70% of raw_corr), the factor is
likely a proxy for monetary conditions rather than a true momentum signal.

Not for use in real-time (§9.8): run during idle/offline phase only.
Results saved to data/causal_approved_factors.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_APPROVED_PATH = Path(__file__).parent.parent.parent / "data" / "causal_approved_factors.json"
_RETENTION_THRESHOLD = 0.70   # partial_corr must be ≥ 70% of raw_corr to pass
_MIN_BARS = 30
_FFR_TICKER = "^IRX"          # 13-week T-bill as Fed Funds Rate proxy


def _fetch_ffr(period: str = "3mo") -> Optional[pd.Series]:
    """Fetch 13-week T-bill rate (FFR proxy) via yfinance."""
    try:
        import yfinance as yf
        df = yf.download(_FFR_TICKER, period=period, interval="1d", progress=False, auto_adjust=True)
        if df is not None and not df.empty:
            col = "Close" if "Close" in df.columns else df.columns[0]
            return df[col].dropna()
    except Exception:
        pass
    return None


def _iv_retention_ratio(factor: np.ndarray, returns: np.ndarray, iv: np.ndarray) -> float:
    """Return partial_corr / raw_corr after removing IV's linear influence from factor.

    Returns 1.0 on error (conservative: keep factor if unsure).
    """
    try:
        n = min(len(factor), len(returns), len(iv))
        if n < _MIN_BARS:
            return 1.0
        f, r, v = factor[-n:], returns[-n:], iv[-n:]

        # Project out IV from factor
        v_c = v - np.nanmean(v)
        f_c = f - np.nanmean(f)
        denom = np.nansum(v_c ** 2)
        if denom < 1e-12:
            return 1.0
        beta = np.nansum(f_c * v_c) / denom
        f_res = f_c - beta * v_c

        raw_corr = float(np.corrcoef(f, r)[0, 1])
        if abs(raw_corr) < 0.005:
            return 1.0  # negligible predictive power — keep factor, don't filter

        partial_corr = float(np.corrcoef(f_res, r - np.nanmean(r))[0, 1])
        return abs(partial_corr) / abs(raw_corr)
    except Exception:
        return 1.0


def screen_factors(
    factor_df: pd.DataFrame,
    close_prices: pd.Series,
    ffr_series: Optional[pd.Series] = None,
) -> dict[str, dict]:
    """Screen factors with IV causal test against Fed Funds Rate.

    Args:
        factor_df: columns = factor names, index = dates
        close_prices: daily close, aligned with factor_df
        ffr_series: FFR proxy; fetched automatically if None

    Returns:
        {factor_name: {raw_corr, retention_ratio, is_causal}}
    """
    if ffr_series is None:
        ffr_series = _fetch_ffr()

    returns = close_prices.pct_change(1).shift(-1)

    results: dict[str, dict] = {}
    for col in factor_df.columns:
        factor = factor_df[col].dropna()
        common = factor.index.intersection(returns.dropna().index)
        if len(common) < _MIN_BARS:
            results[col] = {"is_causal": True, "reason": "insufficient_data",
                            "raw_corr": 0.0, "retention_ratio": 1.0}
            continue

        f_vals = factor.loc[common].values.astype(float)
        r_vals = returns.loc[common].values.astype(float)
        raw_corr = float(np.corrcoef(f_vals, r_vals)[0, 1])

        if ffr_series is None:
            results[col] = {"is_causal": True, "reason": "no_iv",
                            "raw_corr": round(raw_corr, 4), "retention_ratio": 1.0}
            continue

        iv_aligned = ffr_series.reindex(common, method="ffill").ffill().bfill()
        iv_vals = iv_aligned.values.astype(float)
        ratio = _iv_retention_ratio(f_vals, r_vals, iv_vals)

        results[col] = {
            "is_causal": ratio >= _RETENTION_THRESHOLD,
            "raw_corr": round(raw_corr, 4),
            "retention_ratio": round(ratio, 4),
            "reason": "passed" if ratio >= _RETENTION_THRESHOLD else "spurious_iv",
        }

    return results


def get_approved_factors(path: Path = _APPROVED_PATH) -> set[str]:
    """Load causal-approved factor names. Returns empty set if file absent."""
    try:
        data = json.loads(path.read_text())
        return {k for k, v in data.items() if v.get("is_causal", True)}
    except Exception:
        return set()


def save_results(results: dict[str, dict], path: Path = _APPROVED_PATH) -> None:
    """Persist screening results to data/causal_approved_factors.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2))
