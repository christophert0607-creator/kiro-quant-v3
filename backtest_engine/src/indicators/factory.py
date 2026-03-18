from __future__ import annotations

from typing import Any, Dict

import pandas as pd


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_indicator(price_df: pd.DataFrame, name: str, params: Dict[str, Any]) -> pd.DataFrame:
    name = name.upper()
    close = price_df["close"].astype(float)

    try:
        import pandas_ta as ta  # type: ignore
    except Exception:
        ta = None

    if name == "RSI":
        period = int(params.get("period", 14))
        if ta is not None:
            return ta.rsi(close, length=period).to_frame("rsi")
        return _rsi(close, period=period).to_frame("rsi")

    if name == "MACD":
        fast = int(params.get("fast", 12))
        slow = int(params.get("slow", 26))
        signal = int(params.get("signal", 9))
        if ta is not None:
            macd = ta.macd(close, fast=fast, slow=slow, signal=signal)
            return macd
        raise NotImplementedError("MACD requires pandas_ta or TA-Lib")

    raise NotImplementedError(f"Indicator not implemented: {name}")