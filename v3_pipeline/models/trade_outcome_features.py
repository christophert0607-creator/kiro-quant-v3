"""Feature assembly for trade outcome probability heads.

This is a lightweight foundation for future XGBoost/LightGBM heads. It avoids a
hard dependency on those libraries and only builds a deterministic numeric vector
from model hidden state, CL embedding, indicators, and risk context.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class TradeOutcomeFeatures:
    x: np.ndarray
    feature_names: list[str]
    source_flags: dict[str, bool]


def _clean_float(value: object, default: float = 0.0) -> float:
    try:
        out = float(value)  # type: ignore[arg-type]
    except Exception:
        return default
    return out if isfinite(out) else default


def _extend_sequence(values: list[float], names: list[str], prefix: str, seq: Sequence[float] | None) -> bool:
    if seq is None:
        return False
    added = False
    for i, raw in enumerate(seq):
        values.append(_clean_float(raw))
        names.append(f"{prefix}_{i}")
        added = True
    return added


def build_trade_outcome_features(
    *,
    symbol: str,
    market: str,
    lstm_hidden: Sequence[float] | None = None,
    cl_embedding: Sequence[float] | None = None,
    indicators: Mapping[str, float] | None = None,
    risk_context: Mapping[str, float] | None = None,
) -> TradeOutcomeFeatures:
    values: list[float] = []
    names: list[str] = []

    hidden_available = _extend_sequence(values, names, "lstm_hidden", lstm_hidden)
    cl_available = _extend_sequence(values, names, "cl", cl_embedding)

    market_norm = str(market or ("HK" if str(symbol).upper().endswith(".HK") else "US")).upper()
    values.extend([1.0 if market_norm == "HK" else 0.0, 1.0 if market_norm == "US" else 0.0])
    names.extend(["market_HK", "market_US"])

    for key in ("RSI_14", "MACD_HIST", "ATR_14", "BB_POSITION", "SMA_5", "SMA_20"):
        values.append(_clean_float((indicators or {}).get(key)))
        names.append(f"ind_{key}")

    for key in sorted((risk_context or {}).keys()):
        values.append(_clean_float((risk_context or {}).get(key)))
        names.append(f"risk_{key}")

    return TradeOutcomeFeatures(
        x=np.asarray(values, dtype=float),
        feature_names=names,
        source_flags={"hidden_available": hidden_available, "cl_available": cl_available},
    )
