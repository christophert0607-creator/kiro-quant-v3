"""
Centralized yfinance provider for Kiro Quant V3.

All yfinance access in the live runtime MUST go through this module.
Do NOT import yfinance directly in other runtime modules for quote/history fetches.

Features:
- Thread-safe semaphore limiting concurrency (default 1).
- FD guard: reads /proc/self/fd; skips yfinance if usage exceeds 80% of soft limit.
- fd_health_event() for structured telemetry.
"""

from __future__ import annotations

import logging
import os
import resource
import threading
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("kiro.yf_provider")

_MAX_CONCURRENT = int(os.getenv("YF_MAX_CONCURRENT", "1"))
_semaphore = threading.Semaphore(_MAX_CONCURRENT)
_FD_GUARD_THRESHOLD = float(os.getenv("YF_FD_GUARD_THRESHOLD", "0.80"))


# ── FD utilities ──────────────────────────────────────────────────────────────

def _get_fd_stats() -> tuple[int, int]:
    """Return (open_fd_count, soft_limit). Returns (-1, -1) on non-Linux."""
    try:
        open_fds = len(os.listdir("/proc/self/fd"))
    except Exception:
        open_fds = -1
    try:
        soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception:
        soft = -1
    return open_fds, soft


def _top_fd_targets(top_n: int = 5) -> list[str]:
    try:
        fd_dir = Path("/proc/self/fd")
        counts: dict[str, int] = {}
        for fd_entry in fd_dir.iterdir():
            try:
                target = os.readlink(fd_entry)
                counts[target] = counts.get(target, 0) + 1
            except Exception:
                pass
        return [
            f"{t}={n}"
            for t, n in sorted(counts.items(), key=lambda x: -x[1])[:top_n]
        ]
    except Exception:
        return []


def fd_health_event(mode: str = "check") -> dict:
    """Build a structured fd_health event dict for structured logs."""
    open_fds, soft = _get_fd_stats()
    usage_ratio = open_fds / soft if soft > 0 and open_fds >= 0 else 0.0
    return {
        "event": "fd_health",
        "open_fds": open_fds,
        "soft_limit": soft,
        "usage_ratio": round(usage_ratio, 4),
        "top_fd_targets": _top_fd_targets(),
        "mode": mode,
    }


def _is_fd_safe() -> tuple[bool, dict]:
    """Return (safe_to_use, fd_health_dict). False means skip yfinance."""
    event = fd_health_event("pre_fetch")
    safe = event["usage_ratio"] < _FD_GUARD_THRESHOLD or event["open_fds"] < 0
    return safe, event


# ── Public API ────────────────────────────────────────────────────────────────

def get_latest_quote(symbol: str) -> Optional[dict]:
    """
    Fetch latest OHLCV bar for symbol via yfinance.

    Returns None (not raises) when the FD guard triggers or yfinance fails.
    Callers must handle None and fall back to alternative providers.
    """
    safe, health = _is_fd_safe()
    if not safe:
        logger.warning(
            "[yf_provider] FD guard triggered — skipping yfinance for %s "
            "(open_fds=%d soft=%d ratio=%.2f top=%s)",
            symbol, health["open_fds"], health["soft_limit"],
            health["usage_ratio"], health["top_fd_targets"][:2],
        )
        return None

    with _semaphore:
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1d", interval="1m")
            if hist.empty:
                logger.warning("[yf_provider] empty history for %s", symbol)
                return None
            row = hist.iloc[-1]
            close = float(row.get("Close", 0.0))
            return {
                "Date": pd.Timestamp.now(),
                "Open": float(row.get("Open", close)),
                "High": float(row.get("High", close)),
                "Low": float(row.get("Low", close)),
                "Close": close,
                "Volume": float(row.get("Volume", 0.0)),
                "data_source": "YF_LIVE",
            }
        except Exception as exc:
            logger.warning("[yf_provider] get_latest_quote failed for %s: %s", symbol, exc)
            return None


def download_history(
    symbols: list[str],
    period: str = "60d",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """
    Download historical OHLCV for each symbol via yfinance.

    Respects the FD guard and concurrency semaphore.
    Returns dict of symbol -> DataFrame (empty DataFrame on failure).
    """
    if not symbols:
        return {}

    safe, health = _is_fd_safe()
    if not safe:
        logger.warning(
            "[yf_provider] FD guard triggered — skipping history for %d symbols (ratio=%.2f)",
            len(symbols), health["usage_ratio"],
        )
        return {s: pd.DataFrame() for s in symbols}

    results: dict[str, pd.DataFrame] = {}
    with _semaphore:
        try:
            import yfinance as yf
            for sym in symbols:
                try:
                    hist = yf.Ticker(sym).history(period=period, interval=interval)
                    results[sym] = hist if not hist.empty else pd.DataFrame()
                except Exception as exc:
                    logger.warning("[yf_provider] history failed for %s: %s", sym, exc)
                    results[sym] = pd.DataFrame()
        except Exception as exc:
            logger.warning("[yf_provider] download_history error: %s", exc)
            for s in symbols:
                results.setdefault(s, pd.DataFrame())
    return results
