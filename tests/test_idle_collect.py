"""Tests for _collect_only_cycle per-symbol timeout handling.

Bug fixed: asyncio.TimeoutError was caught at the batch level, so one slow
symbol would cause the remaining symbols in that batch to be silently skipped.

Fix: timeout is now caught per-symbol so each symbol is independently
skipped on timeout, and the rest of the batch continues.
"""
import asyncio
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest


# ── module-level stubs so v3_launcher can be imported in a test environment ──

def _make_stub_preparer(**kwargs):
    p = MagicMock()
    p.lookback = kwargs.get("lookback", 2)
    p.is_fitted = True
    p.feature_columns = []
    p.fit_transform = lambda f: f
    return p


sys.modules.setdefault("requests", SimpleNamespace(post=lambda *a, **k: None))
sys.modules.setdefault(
    "v3_pipeline.models.manager",
    SimpleNamespace(
        DataPreparer=_make_stub_preparer,
        ModelManager=object,
    ),
)
sys.modules.setdefault(
    "v3_pipeline.models.brain",
    SimpleNamespace(KiroLSTM=object),
)
sys.modules.setdefault(
    "v3_pipeline.risk.manager",
    SimpleNamespace(RiskConfig=object, RiskController=object),
)
sys.modules.setdefault(
    "v3_pipeline.features.screener",
    SimpleNamespace(V3FunnelScreener=object),
)
sys.modules.setdefault(
    "v3_pipeline.core.market_analyzer",
    SimpleNamespace(MarketAnalyzer=object),
)

from v3_launcher import _collect_only_cycle  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

_COLS = ["Date", "Open", "High", "Low", "Close", "Volume", "data_source"]


def _quote(symbol: str = "SYM", price: float = 100.0) -> dict:
    return {
        "Date": pd.Timestamp("2026-05-08T10:00:00Z"),
        "Open": price,
        "High": price + 1,
        "Low": price - 1,
        "Close": price,
        "Volume": 10_000,
        "data_source": "test",
        "symbol": symbol,
    }


def _make_loop(symbols: list[str], logger=None):
    """Build a minimal loop-like namespace for _collect_only_cycle."""
    buffers = {s: pd.DataFrame(columns=_COLS) for s in symbols}
    connector = MagicMock()
    log = logger or logging.getLogger("test_idle_collect")

    def _normalize(df):
        return df.reset_index(drop=True)

    return SimpleNamespace(
        market_buffers=buffers,
        futu_connector=connector,
        logger=log,
        _normalize_market_buffer=_normalize,
    )


def _make_wait_for(symbol_results: dict):
    """
    Return a patched asyncio.wait_for that delivers results per-symbol.

    symbol_results: maps symbol name → return value or exception instance.
    The call order tracks how many times wait_for was called to figure out
    which symbol is being fetched.
    """
    counter = {"n": 0}
    symbols_in_order = list(symbol_results.keys())

    async def _patched(coro, timeout):
        coro.close()
        sym = symbols_in_order[counter["n"]]
        counter["n"] += 1
        result = symbol_results[sym]
        if isinstance(result, BaseException):
            raise result
        return result

    return _patched


# ── per-symbol timeout isolation ─────────────────────────────────────────────


class TestPerSymbolTimeoutIsolation:
    """A timeout on one symbol must not skip remaining symbols in the batch."""

    def test_timeout_on_first_symbol_does_not_skip_rest(self):
        """When sym0 times out, sym1..sym4 must still be fetched."""
        symbols = [f"SYM{i}" for i in range(5)]
        results = {
            "SYM0": asyncio.TimeoutError(),
            "SYM1": _quote("SYM1"),
            "SYM2": _quote("SYM2"),
            "SYM3": _quote("SYM3"),
            "SYM4": _quote("SYM4"),
        }

        async def _run():
            loop = _make_loop(symbols)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", new_callable=AsyncMock):
                    await _collect_only_cycle(loop, symbols)
            return loop

        loop = asyncio.run(_run())

        assert loop.market_buffers["SYM0"].empty, "SYM0 timed out, should be empty"
        for sym in symbols[1:]:
            assert not loop.market_buffers[sym].empty, f"{sym} should be populated"

    def test_all_symbols_fetched_when_middle_times_out(self):
        """Timeout on a middle symbol skips only that symbol."""
        symbols = ["AAA", "BBB", "CCC"]
        results = {
            "AAA": _quote("AAA"),
            "BBB": asyncio.TimeoutError(),
            "CCC": _quote("CCC"),
        }

        async def _run():
            loop = _make_loop(symbols)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", new_callable=AsyncMock):
                    await _collect_only_cycle(loop, symbols)
            return loop

        loop = asyncio.run(_run())

        assert not loop.market_buffers["AAA"].empty
        assert loop.market_buffers["BBB"].empty
        assert not loop.market_buffers["CCC"].empty

    def test_error_on_one_symbol_does_not_skip_rest(self):
        """RuntimeError on one symbol must not skip remaining symbols."""
        symbols = ["X1", "X2", "X3"]
        results = {
            "X1": RuntimeError("provider unavailable"),
            "X2": _quote("X2"),
            "X3": _quote("X3"),
        }

        async def _run():
            loop = _make_loop(symbols)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", new_callable=AsyncMock):
                    await _collect_only_cycle(loop, symbols)
            return loop

        loop = asyncio.run(_run())

        assert loop.market_buffers["X1"].empty
        assert not loop.market_buffers["X2"].empty
        assert not loop.market_buffers["X3"].empty

    def test_timeout_count_logged_in_warning(self):
        """Warning with timed_out count is emitted when at least one symbol times out."""
        symbols = ["T1", "T2"]
        results = {
            "T1": asyncio.TimeoutError(),
            "T2": _quote("T2"),
        }

        log = logging.getLogger("test.timeout_log")
        warn_msgs = []
        original_warning = log.warning
        log.warning = lambda msg, *a, **k: warn_msgs.append(msg % a if a else msg)

        async def _run():
            loop = _make_loop(symbols, logger=log)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", new_callable=AsyncMock):
                    await _collect_only_cycle(loop, symbols)

        asyncio.run(_run())
        log.warning = original_warning

        assert any("timed_out=1" in m for m in warn_msgs), \
            f"Expected timed_out=1 in warning, got: {warn_msgs}"

    def test_no_warning_when_all_succeed(self):
        """No WARNING is emitted when every symbol returns a valid quote."""
        symbols = ["OK1", "OK2", "OK3"]
        results = {s: _quote(s) for s in symbols}

        log = logging.getLogger("test.no_warn")
        warn_msgs = []
        original_warning = log.warning
        log.warning = lambda msg, *a, **k: warn_msgs.append(msg % a if a else msg)

        async def _run():
            loop = _make_loop(symbols, logger=log)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", new_callable=AsyncMock):
                    await _collect_only_cycle(loop, symbols)

        asyncio.run(_run())
        log.warning = original_warning

        assert not warn_msgs, f"Unexpected warnings: {warn_msgs}"


# ── buffer update logic ───────────────────────────────────────────────────────


class TestBufferUpdate:
    """Buffer update logic: valid quotes are stored, None/empty quotes are ignored."""

    def test_valid_quote_populates_buffer(self):
        """A valid quote dict updates market_buffers for that symbol."""
        symbols = ["VALID"]
        results = {"VALID": _quote("VALID", price=55.0)}

        async def _run():
            loop = _make_loop(symbols)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", new_callable=AsyncMock):
                    await _collect_only_cycle(loop, symbols)
            return loop

        loop = asyncio.run(_run())
        buf = loop.market_buffers["VALID"]
        assert not buf.empty
        assert buf.iloc[-1]["Close"] == 55.0

    def test_none_quote_does_not_crash(self):
        """A None return from get_latest_quote is silently ignored."""
        symbols = ["NONE"]
        results = {"NONE": None}

        async def _run():
            loop = _make_loop(symbols)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", new_callable=AsyncMock):
                    await _collect_only_cycle(loop, symbols)
            return loop

        loop = asyncio.run(_run())
        assert loop.market_buffers["NONE"].empty

    def test_all_nan_quote_does_not_populate_buffer(self):
        """A quote that becomes all-NaN after DataFrame creation is dropped."""
        symbols = ["NAN"]
        results = {
            "NAN": {"Date": None, "Open": None, "High": None, "Low": None,
                    "Close": None, "Volume": None, "data_source": None},
        }

        async def _run():
            loop = _make_loop(symbols)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", new_callable=AsyncMock):
                    await _collect_only_cycle(loop, symbols)
            return loop

        loop = asyncio.run(_run())
        assert loop.market_buffers["NAN"].empty

    def test_consecutive_quotes_appended_to_existing_buffer(self):
        """A second call appends a new row to an already-populated buffer."""
        symbols = ["SERIES"]
        prices = [101.0, 102.0]

        async def _run(price):
            results = {"SERIES": _quote("SERIES", price=price)}
            loop = _make_loop(symbols)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", new_callable=AsyncMock):
                    await _collect_only_cycle(loop, symbols)
            return loop

        loop1 = asyncio.run(_run(prices[0]))
        # Second call uses the populated buffers from first
        loop2 = SimpleNamespace(
            market_buffers=loop1.market_buffers,
            futu_connector=loop1.futu_connector,
            logger=loop1.logger,
            _normalize_market_buffer=loop1._normalize_market_buffer,
        )

        results2 = {"SERIES": _quote("SERIES", price=prices[1])}

        async def _run2():
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results2)):
                with patch("v3_launcher.asyncio.sleep", new_callable=AsyncMock):
                    await _collect_only_cycle(loop2, symbols)

        asyncio.run(_run2())
        assert len(loop2.market_buffers["SERIES"]) == 2


# ── batch sleep behavior ──────────────────────────────────────────────────────


class TestBatchSleepBehavior:
    """Inter-batch sleep is called after each batch regardless of symbol outcome."""

    def test_sleep_called_once_per_batch(self):
        """asyncio.sleep(2.0) is invoked exactly once for a single batch of 3 symbols."""
        symbols = ["A", "B", "C"]
        results = {s: _quote(s) for s in symbols}
        sleep_calls = []

        async def _patched_sleep(t):
            sleep_calls.append(t)

        async def _run():
            loop = _make_loop(symbols)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", side_effect=_patched_sleep):
                    await _collect_only_cycle(loop, symbols)

        asyncio.run(_run())
        assert sleep_calls == [2.0], f"Expected one 2.0-second sleep, got: {sleep_calls}"

    def test_sleep_called_per_batch_across_multiple_batches(self):
        """With 10 symbols (batch_size=5), sleep is called twice."""
        symbols = [f"SYM{i}" for i in range(10)]
        results = {s: _quote(s) for s in symbols}
        sleep_calls = []

        async def _patched_sleep(t):
            sleep_calls.append(t)

        async def _run():
            loop = _make_loop(symbols)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", side_effect=_patched_sleep):
                    await _collect_only_cycle(loop, symbols)

        asyncio.run(_run())
        assert len(sleep_calls) == 2
        assert all(t == 2.0 for t in sleep_calls)

    def test_sleep_still_called_when_all_symbols_timeout(self):
        """Even when all symbols time out, sleep is still called between batches."""
        symbols = ["T1", "T2", "T3"]
        results = {s: asyncio.TimeoutError() for s in symbols}
        sleep_calls = []

        async def _patched_sleep(t):
            sleep_calls.append(t)

        async def _run():
            loop = _make_loop(symbols)
            with patch("v3_launcher.asyncio.wait_for", side_effect=_make_wait_for(results)):
                with patch("v3_launcher.asyncio.sleep", side_effect=_patched_sleep):
                    await _collect_only_cycle(loop, symbols)

        asyncio.run(_run())
        assert sleep_calls == [2.0]
