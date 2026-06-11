"""HKAlpha-1 feature builder.

Pure, deterministic transformation from a featured OHLCV frame
(TechnicalIndicatorGenerator output) plus a market-context mapping into the
stationary feature matrix consumed by the HK prediction model V2.

No price levels leave this module — every feature is a ratio, a z-score, a
bounded oscillator, or a session/time flag, so the model never has to learn a
per-symbol scale. Missing context inputs are filled with 0.0 and reported via
``source_flags`` (same convention as trade_outcome_features.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

# HK session anatomy (HKT). Live buffer Date stamps for .HK symbols are HKT.
HK_OPEN_MINUTES = 9 * 60 + 30      # 09:30
HK_CLOSE_MINUTES = 16 * 60         # 16:00
HK_LUNCH_START = 12 * 60           # 12:00
HK_LUNCH_END = 13 * 60             # 13:00
HK_SESSION_MINUTES = 390.0         # trading minutes per day ex-lunch

_EPS = 1e-9

# Context keys the builder understands. Anything absent is 0.0 + flag=False.
CONTEXT_KEYS = (
    "mom_2800",            # 2800.HK 30m momentum (pulse_momentum convention)
    "mom_3033",            # 3033.HK 30m momentum
    "us_overnight",        # SPY previous-session return
    "posture_risk_on",     # 1.0 if config posture == risk_on
    "prev_close",          # previous session close for gap calc
    "lstm_pred_move",      # (lstm_pred - close) / close from champion model
    "symbol_te",           # target-encoded symbol mean forward return
)

FEATURE_ORDER: list[str] = [
    # price / momentum (ATR-normalized)
    "ret_1n", "ret_5n", "ret_15n", "ret_30n",
    "rsi_14", "macd_hist_n", "bb_position", "sma_ratio",
    "hl_range_rel",
    # volume
    "vol_zscore", "turnover_ratio",
    # HK market context
    "mom_2800", "mom_3033", "us_overnight", "posture_risk_on", "gap_open",
    # session / time
    "min_since_open", "is_pre_lunch", "is_post_lunch_30", "is_last_30",
    "dow_mon", "dow_tue", "dow_wed", "dow_thu", "dow_fri",
    # identity / champion
    "symbol_te", "lstm_pred_move",
    # availability flags (model can learn "context was missing")
    "flag_context", "flag_time", "flag_gap",
]


@dataclass(frozen=True)
class HKAlphaFeatureResult:
    frame: pd.DataFrame              # one row per input bar, columns == FEATURE_ORDER
    feature_names: list[str]
    source_flags: dict[str, bool]


def _clean_series(s: pd.Series) -> pd.Series:
    return s.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _ctx_float(context: Mapping | None, key: str, default: float = 0.0) -> tuple[float, bool]:
    if context is None or key not in context or context[key] is None:
        return default, False
    try:
        v = float(context[key])
    except (TypeError, ValueError):
        return default, False
    if not np.isfinite(v):
        return default, False
    return v, True


def build_hk_alpha_features(
    frame: pd.DataFrame,
    context: Mapping | None = None,
) -> HKAlphaFeatureResult:
    """Build the HKAlpha-1 feature matrix.

    ``frame`` must contain Date/Open/High/Low/Close/Volume and the standard
    indicator columns (RSI_14, MACD_HIST, BB_UPPER/LOWER, ATR_14, SMA_5/20).
    Missing indicator columns degrade to neutral values rather than raising,
    so the live path never crashes on a short warm-up window.
    """
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"hk_alpha_features: missing required columns {missing}")
    if len(frame) == 0:
        raise ValueError("hk_alpha_features: empty frame")

    df = frame.reset_index(drop=True)
    close = pd.to_numeric(df["Close"], errors="coerce").astype(float)
    high = pd.to_numeric(df["High"], errors="coerce").astype(float)
    low = pd.to_numeric(df["Low"], errors="coerce").astype(float)
    open_ = pd.to_numeric(df["Open"], errors="coerce").astype(float)
    volume = pd.to_numeric(df["Volume"], errors="coerce").astype(float)

    def ind(col: str, default: float) -> pd.Series:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").astype(float).fillna(default)
        return pd.Series(default, index=df.index, dtype=float)

    atr = ind("ATR_14", 0.0)
    # ATR ratio used as the volatility unit; floor avoids divide-by-zero on
    # flat warm-up windows.
    atr_ratio = (atr / close.replace(0.0, np.nan)).clip(lower=1e-4).fillna(1e-4)

    out = pd.DataFrame(index=df.index)

    log_close = np.log(close.replace(0.0, np.nan))
    for n in (1, 5, 15, 30):
        raw = log_close.diff(n)
        out[f"ret_{n}n"] = _clean_series(raw / (atr_ratio * np.sqrt(n)))

    out["rsi_14"] = _clean_series(ind("RSI_14", 50.0) / 100.0)
    out["macd_hist_n"] = _clean_series(ind("MACD_HIST", 0.0) / (atr + _EPS)).clip(-5.0, 5.0)

    bb_upper = ind("BB_UPPER", 0.0)
    bb_lower = ind("BB_LOWER", 0.0)
    bb_range = (bb_upper - bb_lower).replace(0.0, np.nan)
    out["bb_position"] = _clean_series((close - bb_lower) / bb_range).clip(0.0, 1.0).where(bb_range.notna(), 0.5)

    sma5 = ind("SMA_5", 0.0)
    sma20 = ind("SMA_20", 0.0).replace(0.0, np.nan)
    out["sma_ratio"] = _clean_series(sma5 / sma20 - 1.0).clip(-0.5, 0.5)

    hl_range = ((high - low) / close.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan)
    hl_mean = hl_range.rolling(20, min_periods=5).mean()
    out["hl_range_rel"] = _clean_series(hl_range / (hl_mean + _EPS)).clip(0.0, 10.0)

    vol_mean = volume.rolling(20, min_periods=5).mean()
    vol_std = volume.rolling(20, min_periods=5).std()
    out["vol_zscore"] = _clean_series((volume - vol_mean) / (vol_std + _EPS)).clip(-5.0, 5.0)
    turnover = volume * close
    out["turnover_ratio"] = _clean_series(turnover / (turnover.rolling(20, min_periods=5).mean() + _EPS)).clip(0.0, 10.0)

    # ── HK market context (scalars broadcast to all rows) ────────────────────
    ctx_flags: dict[str, bool] = {}
    for key in ("mom_2800", "mom_3033", "us_overnight", "posture_risk_on"):
        val, ok = _ctx_float(context, key)
        out[key] = val
        ctx_flags[key] = ok
    context_available = any(ctx_flags.values())

    prev_close, gap_available = _ctx_float(context, "prev_close")
    if gap_available and prev_close > 0:
        gap = float(open_.iloc[0]) / prev_close - 1.0
        out["gap_open"] = gap if np.isfinite(gap) else 0.0
    else:
        out["gap_open"] = 0.0

    # ── session / time ────────────────────────────────────────────────────────
    dates = pd.to_datetime(df["Date"], errors="coerce")
    minutes = dates.dt.hour * 60 + dates.dt.minute
    # Daily bars stamp midnight; treat as "no intraday time available".
    time_available = bool(minutes.notna().any() and (minutes.fillna(0) != 0).any())
    if time_available:
        elapsed = (minutes - HK_OPEN_MINUTES).clip(lower=0)
        # Remove the lunch hour from elapsed trading minutes.
        elapsed = elapsed.where(minutes < HK_LUNCH_END, elapsed - (HK_LUNCH_END - HK_LUNCH_START))
        out["min_since_open"] = _clean_series(elapsed / HK_SESSION_MINUTES).clip(0.0, 1.0)
        out["is_pre_lunch"] = ((minutes >= HK_LUNCH_START - 30) & (minutes < HK_LUNCH_START)).astype(float)
        out["is_post_lunch_30"] = ((minutes >= HK_LUNCH_END) & (minutes < HK_LUNCH_END + 30)).astype(float)
        out["is_last_30"] = ((minutes >= HK_CLOSE_MINUTES - 30) & (minutes < HK_CLOSE_MINUTES)).astype(float)
    else:
        out["min_since_open"] = 0.0
        out["is_pre_lunch"] = 0.0
        out["is_post_lunch_30"] = 0.0
        out["is_last_30"] = 0.0

    dow = dates.dt.dayofweek
    for i, name in enumerate(("dow_mon", "dow_tue", "dow_wed", "dow_thu", "dow_fri")):
        out[name] = (dow == i).astype(float)

    # ── identity / champion ──────────────────────────────────────────────────
    symbol_te, _ = _ctx_float(context, "symbol_te")
    out["symbol_te"] = symbol_te
    lstm_move, _ = _ctx_float(context, "lstm_pred_move")
    out["lstm_pred_move"] = float(np.clip(lstm_move, -0.2, 0.2))

    out["flag_context"] = 1.0 if context_available else 0.0
    out["flag_time"] = 1.0 if time_available else 0.0
    out["flag_gap"] = 1.0 if gap_available else 0.0

    out = out[FEATURE_ORDER].astype(float)
    out = out.replace([np.inf, -np.inf], 0.0).fillna(0.0)

    return HKAlphaFeatureResult(
        frame=out,
        feature_names=list(FEATURE_ORDER),
        source_flags={
            "context_available": context_available,
            "time_available": time_available,
            "gap_available": gap_available,
        },
    )
