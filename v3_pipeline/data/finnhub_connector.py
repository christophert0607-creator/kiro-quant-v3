"""Finnhub quote connector (per-symbol).

Endpoints (v1):
- Quote: /quote?symbol=TSLA
  returns: c(current), o(open), h(high), l(low), pc(prev close), t(epoch sec)

Auth:
- token query param from env FINNHUB_API_KEY

Use-case:
- Fallback provider when Futu/iTick/YF/AV fail.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
FINNHUB_BASE = os.getenv("FINNHUB_BASE", "https://finnhub.io/api/v1").rstrip("/")

# Free tier is limited. Keep it conservative.
# 0.8 req/s ≈ 48 req/min (leaves buffer under ~60/min observed)
FINNHUB_RATE_LIMIT_PER_SEC = float(os.getenv("FINNHUB_RATE_LIMIT_PER_SEC", "0.8"))
FINNHUB_BACKOFF_SEC = float(os.getenv("FINNHUB_BACKOFF_SEC", "10"))
_last_call = 0.0


def _rate_limit() -> None:
    global _last_call
    if FINNHUB_RATE_LIMIT_PER_SEC <= 0:
        return
    now = time.time()
    min_interval = 1.0 / FINNHUB_RATE_LIMIT_PER_SEC
    elapsed = now - _last_call
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_call = time.time()


def get_realtime_quote(symbol: str) -> Optional[dict]:
    """Return normalized quote dict: Date/Open/High/Low/Close/Volume."""
    if not FINNHUB_API_KEY:
        raise RuntimeError("FINNHUB_API_KEY not set")

    _rate_limit()

    params = urllib.parse.urlencode({"symbol": symbol, "token": FINNHUB_API_KEY})
    url = f"{FINNHUB_BASE}/quote?{params}"
    req = urllib.request.Request(url)
    req.add_header("accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        obj = json.loads(raw)

        close = float(obj.get("c", 0) or 0)
        if close <= 0:
            # Sometimes finnhub returns {"error": ...}
            logger.warning("[FINNHUB] Empty quote for %s: %s", symbol, obj)
            return None

        ts = obj.get("t")
        dt = pd.to_datetime(int(ts), unit="s", utc=True) if ts else pd.Timestamp.now(tz="UTC")

        return {
            "Date": dt,
            "Open": float(obj.get("o", close) or close),
            "High": float(obj.get("h", close) or close),
            "Low": float(obj.get("l", close) or close),
            "Close": close,
            "Volume": 0.0,
            "data_source": "FINNHUB_QUOTE",
        }
    except urllib.error.HTTPError as exc:
        # 429 = rate limited, back off then return None so caller can fallback
        if getattr(exc, "code", None) == 429:
            logger.warning("[FINNHUB] 429 rate limit for %s, backing off %.0fs", symbol, FINNHUB_BACKOFF_SEC)
            time.sleep(max(0.0, FINNHUB_BACKOFF_SEC))
            return None
        logger.warning("[FINNHUB] HTTP error for %s: %s", symbol, exc)
        return None
    except Exception as exc:
        logger.warning("[FINNHUB] Quote error for %s: %s", symbol, exc)
        return None
