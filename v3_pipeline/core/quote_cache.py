"""
QuoteCache — centralized in-memory cache for market quotes.

Replaces the per-symbol, per-call provider pattern with a shared TTL cache,
dramatically reducing OpenD/Alltick API load.

Architecture:
  Trader cycle ──→ QuoteCache.get(symbols) ──→ Cache hit → return (0ms)
                                                └──→ Cache miss → refresh_batch() ──→ Alltick/Futu/YF
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

import pandas as pd


def _build_stderr_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


@dataclass
class CacheConfig:
    """Configuration for QuoteCache behaviour."""
    ttl_seconds: float = 30.0          # Cache TTL before stale
    stale_threshold_seconds: float = 15.0  # Log warning if stale > this
    batch_size: int = 10              # Generic batch size (legacy)
    alltick_rate_limit: float = 11.0   # Alltick rate limit + buffer (seconds)
    itick_batch_size: int = 3          # iTick free plan observed: >3 codes returns empty data
    itick_rate_limit: float = 12.0     # iTick free plan: 5 calls/min => 1 call/12s
    futu_timeout: float = 5.0         # Futu quote timeout (seconds)
    yf_timeout: float = 15.0          # yfinance timeout (seconds)
    max_retries: int = 2              # Retry count per symbol on provider failure


@dataclass
class CacheEntry:
    """A single cached quote entry."""
    quote: dict[str, Any]
    ts: float          # Unix timestamp when cached
    provider: str       # Original provider (FUTU, YF, ALLTICK, AV_MCP, etc.)

    def is_fresh(self, ttl: float) -> bool:
        return (time.time() - self.ts) < ttl

    def age_seconds(self) -> float:
        return time.time() - self.ts


class QuoteCache:
    """
    Thread-safe, asyncio-compatible in-memory quote cache.

    Usage:
        cache = QuoteCache(ttl_seconds=30)

        # Read
        quote = cache.get("TSLA")
        if quote is None:
            cache.refresh_batch(["TSLA"], provider_fn=lambda s: futu.get_latest_quote(s))
        quotes = cache.get_many(["TSLA", "NVDA", "AAPL"])

        # Stats
        print(cache.stats())
    """

    def __init__(self, config: Optional[CacheConfig] = None) -> None:
        self.config = config or CacheConfig()
        self._cache: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._stats_hits = 0
        self._stats_misses = 0
        self._stats_stale_hits = 0
        self._logger = _build_stderr_logger(self.__class__.__name__)

    # ── Synchronous read (thread-safe dict access) ────────────────────────────

    def get(self, symbol: str) -> Optional[dict[str, Any]]:
        """
        Get a single cached quote.
        Returns None on cache miss.
        Returns a copy (to prevent mutation of cached data).
        """
        entry = self._cache.get(symbol.upper())
        if entry is None:
            self._stats_misses += 1
            return None

        age = entry.age_seconds()
        if age > self.config.stale_threshold_seconds:
            self._stats_stale_hits += 1
            self._logger.warning(
                "[CACHE][%s] Stale hit — age=%.1fs (threshold=%.1fs) provider=%s",
                symbol, age, self.config.stale_threshold_seconds, entry.provider
            )

        self._stats_hits += 1
        # Return a copy to avoid callers mutating cached data
        return {**entry.quote, "data_source": f"CACHED({entry.provider})"}

    def get_many(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """Batch read — returns dict of {symbol: quote} for all hits."""
        result = {}
        for sym in symbols:
            q = self.get(sym)
            if q is not None:
                result[sym.upper()] = q
        return result

    def get_missing(self, symbols: list[str]) -> list[str]:
        """Return list of symbols not in cache (or stale)."""
        missing = []
        for sym in symbols:
            entry = self._cache.get(sym.upper())
            if entry is None or not entry.is_fresh(self.config.ttl_seconds):
                missing.append(sym.upper())
        return missing

    # ── Synchronous write ───────────────────────────────────────────────────────

    def set(self, symbol: str, quote: dict[str, Any], provider: str = "UNKNOWN") -> None:
        """Cache a quote (thread-safe)."""
        sym = symbol.upper()
        self._cache[sym] = CacheEntry(
            quote=dict(quote),
            ts=time.time(),
            provider=provider,
        )

    def set_many(self, quotes: dict[str, dict[str, Any]], provider: str = "UNKNOWN") -> None:
        """Batch cache write."""
        for sym, quote in quotes.items():
            self.set(sym, quote, provider=provider)

    def invalidate(self, symbol: str) -> None:
        """Force-refresh next read by removing from cache."""
        self._cache.pop(symbol.upper(), None)

    def invalidate_all(self) -> None:
        """Clear entire cache."""
        self._cache.clear()
        self._logger.info("[CACHE] All entries invalidated")

    # ── Bulk refresh via provider function (sync → run in thread) ─────────────

    def refresh_batch(
        self,
        symbols: list[str],
        provider_fn,
        *,
        max_workers: int = 5,
    ) -> dict[str, dict[str, Any]]:
        """
        Refresh missing/stale quotes by calling provider_fn(symbol) for each.

        provider_fn: callable(symbol) -> dict with OHLCV fields
        Runs serially to avoid overwhelming providers.
        Returns dict of {symbol: quote} for successfully fetched quotes.
        """
        missing = self.get_missing(symbols)
        if not missing:
            return {}

        self._logger.info(
            "[CACHE] refresh_batch: %d missing of %d total symbols",
            len(missing), len(symbols)
        )

        results: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}

        for sym in missing:
            for attempt in range(1, self.config.max_retries + 1):
                try:
                    quote = provider_fn(sym)
                    if quote and quote.get("Close", 0) > 0:
                        results[sym] = quote
                        self.set(sym, quote, provider=quote.get("data_source", "UNKNOWN"))
                        break
                    else:
                        errors[sym] = f"attempt {attempt}: empty or invalid quote"
                except Exception as exc:
                    errors[sym] = f"attempt {attempt}: {exc}"
                    self._logger.warning("[CACHE][%s] refresh failed attempt %d: %s", sym, attempt, exc)
                    time.sleep(0.5 * attempt)  # brief backoff
            else:
                self._logger.error("[CACHE][%s] All %d retries exhausted — dropped", sym, self.config.max_retries)

        if errors:
            self._logger.warning("[CACHE] refresh_batch errors: %d symbols failed", len(errors))

        return results

    # ── Async bulk refresh ────────────────────────────────────────────────────

    async def refresh_batch_async(
        self,
        symbols: list[str],
        provider_fn,
        *,
        max_concurrency: int = 5,
    ) -> dict[str, dict[str, Any]]:
        """
        Async version — runs provider calls with a semaphore to limit concurrency.
        provider_fn: async callable(symbol) -> dict
        """
        missing = self.get_missing(symbols)
        if not missing:
            return {}

        self._logger.info(
            "[CACHE] refresh_batch_async: %d missing of %d total",
            len(missing), len(symbols)
        )

        semaphore = asyncio.Semaphore(max_concurrency)
        results: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}

        async def _fetch(sym: str) -> None:
            async with semaphore:
                for attempt in range(1, self.config.max_retries + 1):
                    try:
                        if asyncio.iscoroutinefunction(provider_fn):
                            quote = await provider_fn(sym)
                        else:
                            quote = await asyncio.to_thread(provider_fn, sym)

                        if quote and quote.get("Close", 0) > 0:
                            results[sym] = quote
                            self.set(sym, quote, provider=quote.get("data_source", "UNKNOWN"))
                            return
                        errors[sym] = f"attempt {attempt}: invalid quote"
                    except Exception as exc:
                        errors[sym] = f"attempt {attempt}: {exc}"
                        self._logger.warning("[CACHE][%s] attempt %d failed: %s", sym, attempt, exc)
                        await asyncio.sleep(0.5 * attempt)

                self._logger.error("[CACHE][%s] All retries exhausted", sym)

        await asyncio.gather(*[_fetch(sym) for sym in missing], return_exceptions=True)

        if errors:
            self._logger.warning("[CACHE] %d symbols failed after retries", len(errors))

        return results

    def refresh_batch_itick(
        self,
        symbols: list[str],
        region: str = "US",
        k_type: int = 1,
        limit: int = 2,
    ) -> dict[str, dict[str, Any]]:
        """Refresh quotes using iTick batch klines as primary provider.

        iTick endpoint: /stock/klines
        - k_type: 1=1m, 2=5m, 3=15m, 4=30m, 5=1h, 8=1d
        - batch size depends on plan (Base=10)

        Returns mapping symbol -> normalized quote dict.
        """
        try:
            from v3_pipeline.data.itick_connector import get_stock_klines_batch
        except ImportError:
            self._logger.warning("[CACHE][iTick] itick_connector not available")
            return {}

        results: dict[str, dict[str, Any]] = {}
        batch_size = int(getattr(self.config, "itick_batch_size", 3) or 3)

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            try:
                klines = get_stock_klines_batch(region=region, codes=batch, k_type=k_type, limit=limit)

                for sym, df in klines.items():
                    if df is None or df.empty:
                        continue
                    latest = df.iloc[-1]
                    quote = {
                        "Date": latest.get("Date", pd.Timestamp.now(tz="UTC")),
                        "Open": float(latest.get("Open", 0)),
                        "High": float(latest.get("High", 0)),
                        "Low": float(latest.get("Low", 0)),
                        "Close": float(latest.get("Close", 0)),
                        "Volume": float(latest.get("Volume", 0)),
                        "data_source": "ITICK",
                    }
                    if quote["Close"] > 0:
                        results[sym] = quote
                        self.set(sym, quote, provider="ITICK")

                # Rate-limit between API calls only (no need to sleep after the last batch)
                if i + batch_size < len(symbols):
                    time.sleep(self.config.itick_rate_limit)

            except Exception as exc:
                self._logger.warning("[CACHE][iTick] Batch failed for %s: %s", batch, exc)

        self._logger.info(
            "[CACHE][iTick] Refreshed %d/%d symbols from iTick",
            len(results), len(symbols)
        )
        return results

    def refresh_batch_alltick(
        self,
        symbols: list[str],
        kline_type: int = 1,
        kline_num: int = 2,
    ) -> dict[str, dict[str, Any]]:
        """
        Refresh quotes using Alltick batch-kline API as primary provider.

        Alltick provides real-time kline data with:
        - kline_type: 1=1min, 2=5min, 3=15min, 4=30min, 5=hour, 8=daily
        - Batch of up to 10 symbols per request (built-in rate limit: 1 req / 10s)
        - Returns OHLCV with server-side timestamp

        Falls back to per-symbol provider on failure.
        """
        try:
            from v3_pipeline.data.alltick_connector import get_latest_klines_batch
        except ImportError:
            self._logger.warning("[CACHE][Alltick] alltick_connector not available")
            return {}

        results: dict[str, dict[str, Any]] = {}
        batch_size = self.config.batch_size  # 10

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            try:
                klines = get_latest_klines_batch(batch, kline_type=kline_type, kline_num=kline_num)

                for sym, df in klines.items():
                    if df is None or df.empty:
                        continue
                    # Take the most recent bar
                    latest = df.iloc[-1]
                    quote = {
                        "Date": latest.get("Date", pd.Timestamp.now(tz="UTC")),
                        "Open": float(latest.get("Open", 0)),
                        "High": float(latest.get("High", 0)),
                        "Low": float(latest.get("Low", 0)),
                        "Close": float(latest.get("Close", 0)),
                        "Volume": float(latest.get("Volume", 0)),
                        "data_source": "ALLTICK",
                    }
                    if quote["Close"] > 0:
                        results[sym] = quote
                        self.set(sym, quote, provider="ALLTICK")
                        self._logger.debug("[CACHE][Alltick] %s: close=%.2f", sym, quote["Close"])

                # Rate limit: Alltick free tier = 1 req / 10s
                time.sleep(self.config.alltick_rate_limit)

            except Exception as exc:
                self._logger.warning("[CACHE][Alltick] Batch failed for %s: %s", batch, exc)

        self._logger.info(
            "[CACHE][Alltick] Refreshed %d/%d symbols from Alltick",
            len(results), len(symbols)
        )
        return results

    # ── Statistics ─────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        total = self._stats_hits + self._stats_misses
        hit_rate = self._stats_hits / total if total > 0 else 0.0
        return {
            "hits": self._stats_hits,
            "misses": self._stats_misses,
            "stale_hits": self._stats_stale_hits,
            "total_requests": total,
            "hit_rate_pct": round(hit_rate * 100, 2),
            "entries_cached": len(self._cache),
            "oldest_entry_age": max((e.age_seconds() for e in self._cache.values()), default=0.0),
        }

    def log_stats(self) -> None:
        s = self.stats()
        self._logger.info(
            "[CACHE] hits=%d misses=%d hit_rate=%.1f%% entries=%d oldest=%.1fs",
            s["hits"], s["misses"], s["hit_rate_pct"], s["entries_cached"], s["oldest_entry_age"]
        )

    def reset_stats(self) -> None:
        self._stats_hits = 0
        self._stats_misses = 0
        self._stats_stale_hits = 0

    def debug_dump(self) -> dict[str, Any]:
        """Full cache dump for debugging."""
        return {
            sym: {
                "provider": entry.provider,
                "age_seconds": round(entry.age_seconds(), 2),
                "is_fresh": entry.is_fresh(self.config.ttl_seconds),
                "close": entry.quote.get("Close"),
                "data_source": entry.quote.get("data_source"),
            }
            for sym, entry in self._cache.items()
        }


# ── Standalone test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cache = QuoteCache(config=CacheConfig(ttl_seconds=5))

    # Simulate provider
    def fake_quote(sym: str) -> dict:
        return {
            "Date": datetime.now(),
            "Open": 100.0, "High": 101.0, "Low": 99.0,
            "Close": 100.5, "Volume": 1_000_000,
            "data_source": "FAKE"
        }

    # Miss
    q = cache.get("TSLA")
    print(f"TSLA (miss): {q}")

    # Set
    cache.set("TSLA", fake_quote("TSLA"))

    # Hit
    q = cache.get("TSLA")
    print(f"TSLA (hit): {q}")

    # Stats
    print(f"Stats: {cache.stats()}")
