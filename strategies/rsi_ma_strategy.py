#!/usr/bin/env python3
"""RSI + MA confirmation strategy."""
from __future__ import annotations

from typing import Dict
import pandas as pd
import numpy as np

from .base_strategy import BaseStrategy


def _compute_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


class RSIMAStrategy(BaseStrategy):
    name = "rsi_ma"

    def generate_signal(self, df: pd.DataFrame) -> Dict:
        if df is None or df.empty or "close" not in df.columns:
            return {"signal": "HOLD", "price": 0.0, "meta": {"reason": "no_data"}}

        cfg = self.config or {}
        rsi_period = int(cfg.get("rsi_period", 14))
        ma_period = int(cfg.get("ma_period", 20))
        rsi_buy = float(cfg.get("rsi_buy", 30))
        rsi_sell = float(cfg.get("rsi_sell", 70))
        ma_confirm = bool(cfg.get("ma_confirm", True))

        close = df["close"].astype(float)
        rsi = _compute_rsi(close, rsi_period)
        ma = close.rolling(ma_period).mean()

        last_close = float(close.iloc[-1])
        last_rsi = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else None
        last_ma = float(ma.iloc[-1]) if not np.isnan(ma.iloc[-1]) else None

        if last_rsi is None or last_ma is None:
            return {"signal": "HOLD", "price": last_close, "meta": {"reason": "insufficient_bars"}}

        signal = "HOLD"
        if last_rsi < rsi_buy and (not ma_confirm or last_close > last_ma):
            signal = "BUY"
        elif last_rsi > rsi_sell and (not ma_confirm or last_close < last_ma):
            signal = "SELL"

        return {
            "signal": signal,
            "price": last_close,
            "meta": {
                "rsi": last_rsi,
                "ma": last_ma,
                "rsi_buy": rsi_buy,
                "rsi_sell": rsi_sell,
                "ma_confirm": ma_confirm,
            },
        }

