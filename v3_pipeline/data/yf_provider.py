"""
Centralized yfinance provider for Kiro Quant V3.

All yfinance access in the live runtime MUST go through this module.
Do NOT import yfinance directly in other runtime modules for quote/history fetches.

Features:
- Thread-safe semaphore limiting concurrency (default 1).
- FD guard: reads /proc/self/fd; skips yfinance if usage exceeds 80% of soft limit.
- fd_health_event() for structured telemetry.
- Session cleanup: every Ticker session is explicitly closed after each call
  to prevent FD leaks from yfinance's internal SQLite cache connections.
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
# Cap per-batch FD growth in download_history. Default 5 → reset the yfinance
# Peewee SQLite singletons every N symbols inside the loop instead of only at
# batch end. Without this cap, a 300-symbol IDLE round can balloon FD count
# even with PR #80's per-batch reset because yfinance reopens its tz/cookie
# cache db on every Ticker.history() call.
_RESET_EVERY = max(1, int(os.getenv("YF_RESET_EVERY", "5")))


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


# ── Internal helpers ───────────────────────────────────────────────────────────

# Tokens that quote providers occasionally return in place of a numeric price,
# especially during US after-hours / halt windows. Anything matching these
# (case-insensitive, whitespace-trimmed) is treated as missing data and forces
# the provider to return None so downstream code can fall back instead of
# crashing on `float("N/A")`.
_NON_NUMERIC_PRICE_TOKENS = frozenset({"", "N/A", "NA", "NAN", "NONE", "NULL", "—", "-", "--", "?"})


def _coerce_price(value, *, field: str, symbol: str) -> Optional[float]:
    """Convert ``value`` to a float or return None if it is a sentinel/non-numeric.

    Logs at WARNING when a non-numeric token is rejected so operators see why
    a bar was dropped. Returns None when the value is missing or unparseable.
    """
    if value is None:
        return None
    # pandas/numpy NaN
    try:
        import math
        if isinstance(value, float) and math.isnan(value):
            return None
    except Exception:
        pass
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s.upper() in _NON_NUMERIC_PRICE_TOKENS:
        logger.warning(
            "[yf_provider] %s non-numeric %s=%r — treating as missing data",
            symbol, field, value,
        )
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        logger.warning(
            "[yf_provider] %s unparseable %s=%r — treating as missing data",
            symbol, field, value,
        )
        return None


def _close_ticker_session(ticker) -> None:
    """Close the underlying requests session of a yfinance Ticker.

    yfinance >= 0.2 exposes ``ticker.session`` as a curl_cffi Session.
    Silently no-ops on older versions or when session is unavailable.
    """
    try:
        session = getattr(ticker, "session", None)
        if session is not None and callable(getattr(session, "close", None)):
            session.close()
    except Exception:
        pass


def _reset_yf_cache_fds() -> None:
    """
    Close yfinance's module-level Peewee/SQLite connections to reclaim FDs.

    yfinance 1.x holds three SqliteDatabase singletons (_TzDBManager,
    _CookieDBManager, _ISINDBManager) as module-level class vars. They are
    opened on first use and never closed between Ticker calls, which causes
    tkr-tz.db + tkr-tz.db-wal FDs to accumulate across the process lifetime.

    Calling set_location(same_dir) closes the Peewee db and sets _db=None so
    the next yfinance call re-connects cleanly (one FD pair, not hundreds).
    The paired cache singleton's .db handle and .initialised flag are reset so
    the cache object re-acquires a fresh connection rather than reusing the
    now-dead handle.
    """
    try:
        import yfinance.cache as _yfc

        _PAIRS = (
            ("_TzDBManager",     "_TzCacheManager",     "_tz_cache"),
            ("_CookieDBManager", "_CookieCacheManager", "_Cookie_cache"),
            ("_ISINDBManager",   "_ISINCacheManager",   "_isin_cache"),
        )
        for db_mgr_name, cache_mgr_name, cache_attr in _PAIRS:
            db_mgr = getattr(_yfc, db_mgr_name, None)
            if db_mgr is not None:
                cache_dir = getattr(db_mgr, "_cache_dir", None)
                if cache_dir:
                    db_mgr.set_location(cache_dir)  # close() + _db = None

            cache_mgr = getattr(_yfc, cache_mgr_name, None)
            if cache_mgr is not None:
                singleton = getattr(cache_mgr, cache_attr, None)
                if singleton is not None:
                    singleton.db = None
                    singleton.initialised = -1
    except Exception:
        pass


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

    ticker = None
    with _semaphore:
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="1d", interval="1m")
            if hist.empty:
                logger.warning("[yf_provider] empty history for %s", symbol)
                return None
            row = hist.iloc[-1]
            # Sanitise: providers occasionally return "N/A" or "—" for after-hours
            # bars; let _coerce_price reject those so callers fall back cleanly
            # instead of raising `ValueError: could not convert string to float`
            # in the order-builder.
            close = _coerce_price(row.get("Close"), field="Close", symbol=symbol)
            if close is None:
                return None
            open_v = _coerce_price(row.get("Open"), field="Open", symbol=symbol)
            high_v = _coerce_price(row.get("High"), field="High", symbol=symbol)
            low_v = _coerce_price(row.get("Low"), field="Low", symbol=symbol)
            volume = _coerce_price(row.get("Volume"), field="Volume", symbol=symbol)
            result = {
                "Date": pd.Timestamp.now(),
                "Open": open_v if open_v is not None else close,
                "High": high_v if high_v is not None else close,
                "Low": low_v if low_v is not None else close,
                "Close": close,
                "Volume": volume if volume is not None else 0.0,
                "data_source": "YF_LIVE",
            }
            return result
        except Exception as exc:
            logger.warning("[yf_provider] get_latest_quote failed for %s: %s", symbol, exc)
            return None
        finally:
            if ticker is not None:
                _close_ticker_session(ticker)
            _reset_yf_cache_fds()


def download_history(
    symbols: list[str],
    period: str = "60d",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """
    Download historical OHLCV for each symbol via yfinance.

    Respects the FD guard and concurrency semaphore.
    Returns dict of symbol -> DataFrame (empty DataFrame on failure).

    Session cleanup is performed after each Ticker.history() call to prevent
    FD accumulation from yfinance's SQLite cache connections.
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
            for idx, sym in enumerate(symbols, start=1):
                ticker = None
                try:
                    ticker = yf.Ticker(sym)
                    hist = ticker.history(period=period, interval=interval)
                    results[sym] = hist if not hist.empty else pd.DataFrame()
                except Exception as exc:
                    logger.warning("[yf_provider] history failed for %s: %s", sym, exc)
                    results[sym] = pd.DataFrame()
                finally:
                    if ticker is not None:
                        _close_ticker_session(ticker)
                    # Aggressive reset every N symbols inside the loop to bound
                    # tkr-tz.db FD growth under PR #85's 300+ symbol universe.
                    if idx % _RESET_EVERY == 0:
                        _reset_yf_cache_fds()
        except Exception as exc:
            logger.warning("[yf_provider] download_history error: %s", exc)
            for s in symbols:
                results.setdefault(s, pd.DataFrame())
        finally:
            # Final reset to catch the tail of the loop (when len(symbols) % _RESET_EVERY != 0)
            _reset_yf_cache_fds()
    return results
