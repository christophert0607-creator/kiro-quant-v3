#!/usr/bin/env python3
"""
HK Market Pulse: fetch 30m momentum for 2800.HK (HSI) and 3033.HK (HSTECH).
Returns JSON with momentum values for posture determination.
"""
import json
import sys
from datetime import datetime, timezone, timedelta

import yfinance as yf


def get_30m_momentum(ticker: str, lookback: int = 5) -> float:
    """
    Fetch 5d of 30-minute bars and compute net momentum from the last `lookback` closes.
    momentum = (latest_close - earliest_close) / earliest_close
    Returns 0.0 on error.
    """
    try:
        obj = yf.Ticker(ticker)
        df = obj.history(
            interval="30m",
            period="5d",
            auto_adjust=True,
            keepna=False
        )
        if df.empty or len(df) < 2:
            return 0.0
        closes = df["Close"].tail(lookback).values
        if len(closes) < 2:
            return 0.0
        mom = (closes[-1] - closes[0]) / closes[0]
        return float(mom)
    except Exception:
        return 0.0


def main():
    mom_2800 = get_30m_momentum("2800.HK")
    mom_3033 = get_30m_momentum("3033.HK")

    result = {
        "2800.HK": round(mom_2800, 6),
        "3033.HK": round(mom_3033, 6),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
