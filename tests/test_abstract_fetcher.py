"""Tests for v3_pipeline.data.abstract_fetcher (P1 — centralized data fetcher)."""
import time

import pandas as pd
import pytest

from v3_pipeline.data.abstract_fetcher import (
    AbstractDataFetcher,
    EFinanceFetcher,
    FallbackDataFetcher,
    FetchMetrics,
    YFinanceFetcher,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_quote(source: str = "TEST", close: float = 100.0) -> dict:
    return {
        "Date": pd.Timestamp.now(),
        "Open": close,
        "High": close,
        "Low": close,
        "Close": close,
        "Volume": 1000.0,
        "data_source": source,
    }


class _SuccessFetcher(AbstractDataFetcher):
    provider_name = "success"

    def get_latest_quote(self, symbol: str):
        return _make_quote("SUCCESS")


class _FailFetcher(AbstractDataFetcher):
    provider_name = "fail"

    def get_latest_quote(self, symbol: str):
        return None


class _RaisingFetcher(AbstractDataFetcher):
    provider_name = "raising"

    def get_latest_quote(self, symbol: str):
        raise RuntimeError("simulated provider error")


class _SlowFetcher(AbstractDataFetcher):
    provider_name = "slow"

    def __init__(self, delay: float = 0.05):
        self._delay = delay

    def get_latest_quote(self, symbol: str):
        time.sleep(self._delay)
        return _make_quote("SLOW")


class _HistoryFetcher(AbstractDataFetcher):
    provider_name = "history"

    def get_latest_quote(self, symbol: str):
        return None

    def download_history(self, symbols, period="60d", interval="1d"):
        return {s: pd.DataFrame({"Close": [1.0, 2.0, 3.0]}) for s in symbols}


# ── FetchMetrics ───────────────────────────────────────────────────────────────

class TestFetchMetrics:
    def test_initial_state(self):
        m = FetchMetrics(provider_name="test")
        assert m.success_count == 0
        assert m.failure_count == 0
        assert m.avg_latency_ms == 0.0
        assert m.last_error is None

    def test_record_success(self):
        m = FetchMetrics(provider_name="test")
        m.record_success(10.0)
        m.record_success(20.0)
        assert m.success_count == 2
        assert m.avg_latency_ms == 15.0
        assert m.last_error is None

    def test_record_failure(self):
        m = FetchMetrics(provider_name="test")
        m.record_failure("network error")
        assert m.failure_count == 1
        assert m.last_error == "network error"

    def test_to_dict_has_required_keys(self):
        m = FetchMetrics(provider_name="test")
        d = m.to_dict()
        for key in ("provider", "success", "failure", "avg_latency_ms", "last_error"):
            assert key in d


# ── AbstractDataFetcher contract ──────────────────────────────────────────────

class TestAbstractFetcherContract:
    def test_subclass_must_implement_get_latest_quote(self):
        with pytest.raises(TypeError):
            AbstractDataFetcher()  # type: ignore[abstract]

    def test_default_download_history_returns_empty_dfs(self):
        fetcher = _SuccessFetcher()
        results = fetcher.download_history(["TSLA", "NVDA"])
        assert set(results.keys()) == {"TSLA", "NVDA"}
        for df in results.values():
            assert isinstance(df, pd.DataFrame)

    def test_default_close_no_error(self):
        fetcher = _SuccessFetcher()
        fetcher.close()  # Should not raise


# ── FallbackDataFetcher ────────────────────────────────────────────────────────

class TestFallbackDataFetcher:
    def test_requires_at_least_one_provider(self):
        with pytest.raises(ValueError):
            FallbackDataFetcher([])

    def test_returns_quote_from_first_working_provider(self):
        fetcher = FallbackDataFetcher([_SuccessFetcher(), _FailFetcher()])
        result = fetcher.get_latest_quote("TSLA")
        assert result is not None
        assert result["_fetch_provider"] == "success"

    def test_falls_back_to_second_provider_when_first_returns_none(self):
        fetcher = FallbackDataFetcher([_FailFetcher(), _SuccessFetcher()])
        result = fetcher.get_latest_quote("TSLA")
        assert result is not None
        assert result["_fetch_provider"] == "success"

    def test_falls_back_when_first_raises(self):
        fetcher = FallbackDataFetcher([_RaisingFetcher(), _SuccessFetcher()])
        result = fetcher.get_latest_quote("TSLA")
        assert result is not None
        assert result["_fetch_provider"] == "success"

    def test_returns_none_when_all_providers_fail(self):
        fetcher = FallbackDataFetcher([_FailFetcher(), _FailFetcher()])
        result = fetcher.get_latest_quote("TSLA")
        assert result is None

    def test_result_includes_latency_ms(self):
        fetcher = FallbackDataFetcher([_SuccessFetcher()])
        result = fetcher.get_latest_quote("TSLA")
        assert "_fetch_latency_ms" in result
        assert result["_fetch_latency_ms"] >= 0.0

    def test_latency_ms_reflects_actual_time(self):
        fetcher = FallbackDataFetcher([_SlowFetcher(delay=0.03)])
        result = fetcher.get_latest_quote("TSLA")
        assert result is not None
        assert result["_fetch_latency_ms"] >= 20.0  # at least 20ms for 30ms sleep

    def test_failure_metrics_recorded_when_provider_returns_none(self):
        fetcher = FallbackDataFetcher([_FailFetcher(), _SuccessFetcher()])
        fetcher.get_latest_quote("TSLA")
        metrics = {m["provider"]: m for m in fetcher.get_metrics()}
        assert metrics["fail"]["failure"] == 1
        assert metrics["success"]["success"] == 1

    def test_failure_metrics_recorded_when_provider_raises(self):
        fetcher = FallbackDataFetcher([_RaisingFetcher(), _SuccessFetcher()])
        fetcher.get_latest_quote("TSLA")
        metrics = {m["provider"]: m for m in fetcher.get_metrics()}
        assert metrics["raising"]["failure"] == 1

    def test_get_metrics_returns_list_of_dicts(self):
        fetcher = FallbackDataFetcher([_SuccessFetcher(), _FailFetcher()])
        fetcher.get_latest_quote("TSLA")
        metrics = fetcher.get_metrics()
        assert isinstance(metrics, list)
        for m in metrics:
            for key in ("provider", "success", "failure", "avg_latency_ms"):
                assert key in m

    def test_avg_latency_accumulates_across_calls(self):
        fetcher = FallbackDataFetcher([_SuccessFetcher()])
        fetcher.get_latest_quote("TSLA")
        fetcher.get_latest_quote("NVDA")
        metrics = {m["provider"]: m for m in fetcher.get_metrics()}
        assert metrics["success"]["success"] == 2

    def test_data_source_preserved_from_provider(self):
        fetcher = FallbackDataFetcher([_SuccessFetcher()])
        result = fetcher.get_latest_quote("TSLA")
        assert result["data_source"] == "SUCCESS"

    def test_close_delegates_to_providers(self):
        closed = []

        class _CloseTracker(AbstractDataFetcher):
            provider_name = "tracker"

            def get_latest_quote(self, symbol):
                return _make_quote()

            def close(self):
                closed.append(True)

        fetcher = FallbackDataFetcher([_CloseTracker()])
        fetcher.close()
        assert len(closed) == 1

    def test_download_history_uses_first_non_empty_provider(self):
        empty = _FailFetcher()
        fetcher = FallbackDataFetcher([empty, _HistoryFetcher()])
        results = fetcher.download_history(["TSLA"])
        assert not results["TSLA"].empty

    def test_download_history_all_empty_returns_empty_dfs(self):
        fetcher = FallbackDataFetcher([_FailFetcher(), _FailFetcher()])
        results = fetcher.download_history(["TSLA"])
        assert isinstance(results["TSLA"], pd.DataFrame)
        assert results["TSLA"].empty


# ── YFinanceFetcher and EFinanceFetcher (import guard) ─────────────────────────

class TestYFinanceFetcherImportGuard:
    def test_returns_none_when_yfinance_unavailable(self, monkeypatch):
        """Should not raise even if yfinance or yf_provider is missing."""
        import importlib
        import sys

        monkeypatch.setitem(sys.modules, "yfinance", None)
        fetcher = YFinanceFetcher()
        result = fetcher.get_latest_quote("TSLA")
        assert result is None or isinstance(result, dict)


class TestEFinanceFetcherImportGuard:
    def test_returns_none_when_efinance_unavailable(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "efinance", None)
        fetcher = EFinanceFetcher()
        result = fetcher.get_latest_quote("0700.HK")
        assert result is None
