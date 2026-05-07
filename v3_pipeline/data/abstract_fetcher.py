"""
AbstractDataFetcher — centralized data provider contract for Kiro Quant V3.

All quote/history providers implement AbstractDataFetcher.
FallbackDataFetcher chains multiple providers with observable latency and source metrics.

Usage:
    from v3_pipeline.data.abstract_fetcher import FallbackDataFetcher, FetchResult
    fetcher = FallbackDataFetcher(providers=[yf_fetcher, futu_fetcher])
    result = fetcher.get_latest_quote("TSLA")
    if result:
        print(result.quote, result.source, result.latency_ms)
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

logger = logging.getLogger("kiro.abstract_fetcher")


@dataclass
class FetchResult:
    """Normalized quote result with provenance and latency."""
    quote: dict
    source: str
    latency_ms: float
    symbol: str
    provider_name: str


@dataclass
class FetchMetrics:
    """Accumulated per-provider fetch metrics for observability."""
    provider_name: str
    success_count: int = 0
    failure_count: int = 0
    total_latency_ms: float = 0.0
    last_error: Optional[str] = None

    @property
    def avg_latency_ms(self) -> float:
        if self.success_count == 0:
            return 0.0
        return self.total_latency_ms / self.success_count

    def record_success(self, latency_ms: float) -> None:
        self.success_count += 1
        self.total_latency_ms += latency_ms
        self.last_error = None

    def record_failure(self, error: str) -> None:
        self.failure_count += 1
        self.last_error = error

    def to_dict(self) -> dict:
        return {
            "provider": self.provider_name,
            "success": self.success_count,
            "failure": self.failure_count,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "last_error": self.last_error,
        }


class AbstractDataFetcher(ABC):
    """Base class for all data providers.

    Subclasses implement get_latest_quote() and optionally download_history().
    The provider_name is used in FetchResult and FetchMetrics for observability.
    """

    provider_name: str = "unknown"

    @abstractmethod
    def get_latest_quote(self, symbol: str) -> Optional[dict]:
        """Return a normalized quote dict or None on failure.

        The returned dict should contain at minimum:
            Close, Open, High, Low, Volume, Date, data_source
        """

    def download_history(
        self,
        symbols: list[str],
        period: str = "60d",
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        """Return historical OHLCV for each symbol. Default: empty dict."""
        return {s: pd.DataFrame() for s in symbols}

    def close(self) -> None:
        """Release any held resources. Default: no-op."""


class YFinanceFetcher(AbstractDataFetcher):
    """yfinance provider — wraps the centralized yf_provider module."""

    provider_name = "yfinance"

    def get_latest_quote(self, symbol: str) -> Optional[dict]:
        try:
            from v3_pipeline.data.yf_provider import get_latest_quote as _yf_get
            return _yf_get(symbol)
        except Exception as exc:
            logger.warning("[YFinanceFetcher] get_latest_quote failed for %s: %s", symbol, exc)
            return None

    def download_history(
        self,
        symbols: list[str],
        period: str = "60d",
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        try:
            from v3_pipeline.data.yf_provider import download_history as _yf_dl
            return _yf_dl(symbols, period=period, interval=interval)
        except Exception as exc:
            logger.warning("[YFinanceFetcher] download_history failed: %s", exc)
            return {s: pd.DataFrame() for s in symbols}


class EFinanceFetcher(AbstractDataFetcher):
    """efinance provider for CN/HK stock quotes."""

    provider_name = "efinance"

    def get_latest_quote(self, symbol: str) -> Optional[dict]:
        try:
            import efinance as ef  # type: ignore
            df = ef.stock.get_latest_quote([symbol])
            if df is None or df.empty:
                return None
            row = df.iloc[0]
            close = float(row.get("最新价", row.get("昨收", 0.0)))
            return {
                "Date": pd.Timestamp.now(),
                "Open": float(row.get("今开", close)),
                "High": float(row.get("最高", close)),
                "Low": float(row.get("最低", close)),
                "Close": close,
                "Volume": float(row.get("成交量", 0.0)),
                "data_source": "EF_LIVE",
            }
        except Exception as exc:
            logger.warning("[EFinanceFetcher] get_latest_quote failed for %s: %s", symbol, exc)
            return None


class FutuQuoteFetcher(AbstractDataFetcher):
    """Futu OpenD quote provider — wraps a connected FutuConnector instance."""

    provider_name = "futu"

    def __init__(self, connector: object) -> None:
        self._connector = connector

    def get_latest_quote(self, symbol: str) -> Optional[dict]:
        try:
            quote = self._connector.get_latest_quote(symbol, use_cache=False)  # type: ignore[attr-defined]
            if quote:
                quote = dict(quote)
                quote["data_source"] = "FUTU"
            return quote
        except Exception as exc:
            logger.warning("[FutuQuoteFetcher] get_latest_quote failed for %s: %s", symbol, exc)
            return None


class FallbackDataFetcher(AbstractDataFetcher):
    """Chain multiple providers; try each in order, emit latency/source metrics.

    Example:
        fetcher = FallbackDataFetcher([YFinanceFetcher(), EFinanceFetcher()])
        result = fetcher.get_latest_quote("0700.HK")
        # result.source == "efinance" if yfinance returns None for HK
    """

    provider_name = "fallback"

    def __init__(self, providers: list[AbstractDataFetcher]) -> None:
        if not providers:
            raise ValueError("FallbackDataFetcher requires at least one provider")
        self._providers = providers
        self._metrics: dict[str, FetchMetrics] = {
            p.provider_name: FetchMetrics(provider_name=p.provider_name)
            for p in providers
        }

    def get_latest_quote(self, symbol: str) -> Optional[dict]:
        for provider in self._providers:
            t0 = time.monotonic()
            try:
                quote = provider.get_latest_quote(symbol)
            except Exception as exc:
                latency_ms = (time.monotonic() - t0) * 1000
                self._metrics[provider.provider_name].record_failure(str(exc))
                logger.warning(
                    "[FallbackDataFetcher] provider=%s symbol=%s error=%s latency_ms=%.1f",
                    provider.provider_name, symbol, exc, latency_ms,
                )
                continue

            latency_ms = (time.monotonic() - t0) * 1000

            if quote is not None:
                self._metrics[provider.provider_name].record_success(latency_ms)
                logger.debug(
                    "[FallbackDataFetcher] provider=%s symbol=%s latency_ms=%.1f",
                    provider.provider_name, symbol, latency_ms,
                )
                return {
                    **quote,
                    "data_source": quote.get("data_source", provider.provider_name),
                    "_fetch_latency_ms": round(latency_ms, 2),
                    "_fetch_provider": provider.provider_name,
                }
            else:
                self._metrics[provider.provider_name].record_failure("returned None")

        logger.warning(
            "[FallbackDataFetcher] all providers failed for symbol=%s providers=%s",
            symbol,
            [p.provider_name for p in self._providers],
        )
        return None

    def download_history(
        self,
        symbols: list[str],
        period: str = "60d",
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        for provider in self._providers:
            t0 = time.monotonic()
            try:
                results = provider.download_history(symbols, period=period, interval=interval)
                latency_ms = (time.monotonic() - t0) * 1000
                non_empty = sum(1 for df in results.values() if not df.empty)
                if non_empty > 0:
                    self._metrics[provider.provider_name].record_success(latency_ms)
                    logger.debug(
                        "[FallbackDataFetcher] history provider=%s symbols=%d non_empty=%d latency_ms=%.1f",
                        provider.provider_name, len(symbols), non_empty, latency_ms,
                    )
                    return results
                else:
                    self._metrics[provider.provider_name].record_failure("all empty DataFrames")
            except Exception as exc:
                latency_ms = (time.monotonic() - t0) * 1000
                self._metrics[provider.provider_name].record_failure(str(exc))
                logger.warning(
                    "[FallbackDataFetcher] history provider=%s error=%s latency_ms=%.1f",
                    provider.provider_name, exc, latency_ms,
                )
        return {s: pd.DataFrame() for s in symbols}

    def get_metrics(self) -> list[dict]:
        """Return per-provider metrics as a list of dicts for structured logging."""
        return [m.to_dict() for m in self._metrics.values()]

    def close(self) -> None:
        for provider in self._providers:
            try:
                provider.close()
            except Exception:
                pass
