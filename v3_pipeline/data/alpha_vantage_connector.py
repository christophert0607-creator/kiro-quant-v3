"""
Alpha Vantage real-time price connector (free tier).
Use for: GLOBAL_QUOTE (real-time price, free)
NOT for: intraday data (premium only)
"""
import logging, time, os
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

AV_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "Q81E7UZE4J71VEZN")
AV_RATE_LIMIT = 5  # 5 calls/minute on free tier
_last_av_call = 0.0


def _rate_limit():
    global _last_av_call
    now = time.time()
    elapsed = now - _last_av_call
    if elapsed < (60.0 / AV_RATE_LIMIT):
        time.sleep((60.0 / AV_RATE_LIMIT) - elapsed)
    _last_av_call = time.time()


def get_realtime_quote(symbol: str) -> Optional[dict]:
    """
    Get real-time quote for a US stock symbol via Alpha Vantage GLOBAL_QUOTE.
    Returns dict with: {Date, Open, High, Low, Close, Volume, data_source}
    """
    try:
        _rate_limit()
        import urllib.request
        import json

        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={AV_API_KEY}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())

        gq = result.get("Global Quote", {})
        if not gq:
            logger.warning("[AV] Empty quote for %s: %s", symbol, result)
            return None

        return {
            "Date": pd.Timestamp.now(),
            "Open": float(gq.get("02. open", 0)),
            "High": float(gq.get("03. high", 0)),
            "Low": float(gq.get("04. low", 0)),
            "Close": float(gq.get("05. price", 0)),
            "Volume": float(gq.get("06. volume", 0)),
            "data_source": f"AV_GLOBAL_QUOTE",
        }
    except Exception as exc:
        logger.warning("[AV] Quote error for %s: %s", symbol, exc)
        return None


def get_daily_bars(symbol: str, outputsize: str = "compact") -> pd.DataFrame:
    """
    Get daily OHLCV bars for a symbol.
    outputsize='compact' = last 100 days, 'full' = 20+ years.
    """
    try:
        _rate_limit()
        import urllib.request
        import json

        url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={symbol}&outputsize={outputsize}&apikey={AV_API_KEY}&datatype=json"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())

        ts_key = "Time Series (Daily)"
        if ts_key not in result:
            logger.warning("[AV] No daily data for %s: %s", symbol, result)
            return pd.DataFrame()

        rows = []
        for dt, vals in result[ts_key].items():
            rows.append({
                "Date": pd.Timestamp(dt),
                "Open": float(vals["1. open"]),
                "High": float(vals["2. high"]),
                "Low": float(vals["3. low"]),
                "Close": float(vals["4. close"]),
                "Volume": float(vals["5. volume"]),
                "data_source": "AV_DAILY",
            })

        df = pd.DataFrame(rows)
        df = df.sort_values("Date").drop_duplicates(subset=["Date"], keep="last").reset_index(drop=True)
        # Add Dividends and Stock Splits columns (always 0 for US stocks in AV)
        df["Dividends"] = 0.0
        df["Stock Splits"] = 0.0
        return df
    except Exception as exc:
        logger.warning("[AV] Daily bars error for %s: %s", symbol, exc)
        return pd.DataFrame()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Test GLOBAL_QUOTE
    print("Testing GLOBAL_QUOTE for TSLA...")
    q = get_realtime_quote("TSLA")
    print(f"TSLA: {q}")
    print()
    print("Testing daily bars for TSLA (compact)...")
    df = get_daily_bars("TSLA", "compact")
    print(f"Got {len(df)} rows, from {df.iloc[0]['Date']} to {df.iloc[-1]['Date']}")
    print(df.tail(3))
