"""iTick REST API connector for batch stock klines.

Use-case:
- Replace broken Alltick batch-kline with iTick /stock/klines.
- Fetch latest 1m (or other interval) klines for up to N symbols per request (plan-limited).

Docs:
- https://docs.itick.org/en/rest-api/stocks/stock-klines

Auth:
- Header: token: <ITICK_TOKEN>

Notes:
- This returns kline bars (OHLCV) not a dedicated quote endpoint.
- We convert the most recent bar into a quote upstream.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

ITICK_TOKEN = os.getenv("ITICK_TOKEN", "").strip()
ITICK_BASE = os.getenv("ITICK_BASE", "https://api.itick.org").rstrip("/")

# Be conservative by default to avoid plan/rate-limit surprises.
ITICK_RATE_LIMIT_SEC = float(os.getenv("ITICK_RATE_LIMIT_SEC", "1.0"))
_last_call = 0.0


def _rate_limit() -> None:
    global _last_call
    now = time.time()
    elapsed = now - _last_call
    if elapsed < ITICK_RATE_LIMIT_SEC:
        time.sleep(ITICK_RATE_LIMIT_SEC - elapsed)
    _last_call = time.time()


def _request_json(path: str, params: dict, timeout: int = 15) -> dict:
    if not ITICK_TOKEN:
        raise RuntimeError("ITICK_TOKEN not set")

    _rate_limit()

    qs = urllib.parse.urlencode(params)
    url = f"{ITICK_BASE}{path}?{qs}"

    req = urllib.request.Request(url)
    req.add_header("accept", "application/json")
    req.add_header("token", ITICK_TOKEN)

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"iTick JSON decode failed: {exc}; raw={raw[:200]}")


def get_stock_klines_batch(
    region: str,
    codes: list[str],
    k_type: int = 1,
    limit: int = 2,
) -> dict[str, pd.DataFrame]:
    """Fetch batch klines for multiple stock codes.

    region: e.g. US, HK
    k_type: 1=1m, 2=5m, 3=15m, 4=30m, 5=1h, 8=1d, 9=1w, 10=1mo
    limit: number of bars

    Returns: mapping code -> DataFrame with columns Date/Open/High/Low/Close/Volume.
    """
    if not codes:
        return {}

    payload = {
        "region": region.upper(),
        "codes": ",".join(codes),
        "kType": str(int(k_type)),
        "limit": str(int(limit)),
    }

    try:
        result = _request_json("/stock/klines", payload)
    except Exception as exc:
        logger.error("[iTick] batch klines request failed for %s: %s", codes, exc)
        return {}

    if result.get("code") != 0:
        logger.warning("[iTick] API error: %s", result)
        return {}

    data = result.get("data") or {}
    out: dict[str, pd.DataFrame] = {}

    for code, bars in data.items():
        if not bars:
            continue
        rows = []
        for b in bars:
            try:
                ts_ms = int(b.get("t", 0) or 0)
                rows.append({
                    "Date": pd.to_datetime(ts_ms, unit="ms", utc=True),
                    "Open": float(b.get("o", 0) or 0),
                    "High": float(b.get("h", 0) or 0),
                    "Low": float(b.get("l", 0) or 0),
                    "Close": float(b.get("c", 0) or 0),
                    "Volume": float(b.get("v", 0) or 0),
                    "data_source": "ITICK_KLINE",
                })
            except Exception:
                continue
        if rows:
            df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
            out[str(code)] = df

    return out
