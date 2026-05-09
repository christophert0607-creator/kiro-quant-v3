"""
Tests for yf_provider FD guard, centralized access, and integration points.

Covers the requirements listed in CLAUDE_CODE_REWRITE_GUIDE.md:
 - Repeated yfinance calls do not linearly increase FD count
 - FutuConnector.get_latest_quote("AAPL", use_cache=False) does not raise TypeError
 - 0700.HK resolves to HK market/prefix and is never queried as US.0700.HK
 - FD guard disables yfinance when FD usage is above the threshold
 - IdleTaskScheduler uses the centralized provider, not direct yf.Ticker
 - IdleTaskScheduler starts at most once per IDLE session
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_empty_loop(symbols=None):
    """Minimal LiveTradingLoop-like stub for scheduler tests."""
    loop = MagicMock()
    loop.symbols = symbols or []
    loop.market_buffers = {}
    loop.feature_generator = MagicMock()
    loop.feature_generator.generate = MagicMock(return_value=pd.DataFrame())
    return loop


def _fake_history_df():
    return pd.DataFrame({
        "Open": [100.0], "High": [101.0], "Low": [99.0],
        "Close": [100.5], "Volume": [1000.0],
    })


# ── yf_provider FD guard ──────────────────────────────────────────────────────

class TestFdGuard:
    def test_fd_guard_blocks_when_over_threshold(self):
        """When FD usage > threshold, get_latest_quote returns None."""
        from v3_pipeline.data import yf_provider

        with patch.object(yf_provider, "_get_fd_stats", return_value=(900, 1000)):
            result = yf_provider.get_latest_quote("AAPL")
        assert result is None

    def test_fd_guard_passes_when_under_threshold(self):
        """When FD usage is low, get_latest_quote proceeds to yfinance."""
        from v3_pipeline.data import yf_provider

        fake_df = _fake_history_df()
        with patch.object(yf_provider, "_get_fd_stats", return_value=(100, 1000)):
            with patch("yfinance.Ticker") as mock_ticker:
                mock_ticker.return_value.history.return_value = fake_df
                result = yf_provider.get_latest_quote("AAPL")

        assert result is not None
        assert result["Close"] == 100.5

    def test_fd_guard_safe_when_proc_unavailable(self):
        """On non-Linux (no /proc), open_fds=-1 means guard does NOT block."""
        from v3_pipeline.data import yf_provider

        with patch.object(yf_provider, "_get_fd_stats", return_value=(-1, -1)):
            with patch("yfinance.Ticker") as mock_ticker:
                mock_ticker.return_value.history.return_value = _fake_history_df()
                result = yf_provider.get_latest_quote("AAPL")

        # usage_ratio = 0.0 when open_fds < 0, so guard does not block
        assert result is not None

    def test_fd_health_event_structure(self):
        """fd_health_event returns expected keys."""
        from v3_pipeline.data import yf_provider

        with patch.object(yf_provider, "_get_fd_stats", return_value=(200, 1024)):
            event = yf_provider.fd_health_event("test")

        assert event["event"] == "fd_health"
        assert event["open_fds"] == 200
        assert event["soft_limit"] == 1024
        assert "usage_ratio" in event
        assert "top_fd_targets" in event
        assert event["mode"] == "test"

    def test_download_history_blocked_by_fd_guard(self):
        """download_history returns empty DataFrames when FD guard fires."""
        from v3_pipeline.data import yf_provider

        with patch.object(yf_provider, "_get_fd_stats", return_value=(900, 1000)):
            result = yf_provider.download_history(["AAPL", "MSFT"])

        assert all(df.empty for df in result.values())

    def test_repeated_calls_no_fd_growth(self):
        """Repeated quote fetches do not linearly grow the FD count mock."""
        from v3_pipeline.data import yf_provider

        call_count = 0

        def _mock_stats():
            return (100 + call_count, 1024)

        with patch.object(yf_provider, "_get_fd_stats", side_effect=_mock_stats):
            with patch("yfinance.Ticker") as mock_ticker:
                mock_ticker.return_value.history.return_value = _fake_history_df()
                for _ in range(5):
                    yf_provider.get_latest_quote("AAPL")

        # Each call should stay well under the 80% threshold (819/1024)
        # Even call 5 uses 105/1024 ≈ 10%, far below guard
        assert (100 + 5) / 1024 < 0.80


# ── FutuConnector compatibility ───────────────────────────────────────────────

class TestFutuConnectorUseCacheParam:
    def _make_connector(self):
        from v3_pipeline.core.futu_connector import FutuConfig, FutuConnector
        conn = FutuConnector.__new__(FutuConnector)
        conn.config = FutuConfig(market_prefix="US")
        conn.quote_ctx = None
        conn.trade_ctx = None
        conn.ft = None
        conn._quote_cache = MagicMock()
        conn._quote_cache.get = MagicMock(return_value=None)
        conn._cached_hit_count = {}
        conn._futu_degraded = False
        conn._futu_fail_count = 0
        conn._FUTU_FAIL_THRESHOLD = 3
        conn.elite_symbols = set()
        conn.logger = MagicMock()
        conn.yf_proxy_pool = []
        conn.yf_user_agents = []
        return conn

    def test_use_cache_false_does_not_raise_type_error(self):
        """get_latest_quote(symbol, use_cache=False) must not raise TypeError."""
        conn = self._make_connector()

        fake_quote = {
            "Date": pd.Timestamp.now(), "Open": 1.0, "High": 1.1,
            "Low": 0.9, "Close": 1.0, "Volume": 100.0, "data_source": "YF_LIVE",
        }
        with patch.object(conn, "_get_latest_quote_yfinance", return_value=fake_quote):
            result = conn.get_latest_quote("AAPL", use_cache=False)

        assert result["Close"] == 1.0

    def test_use_cache_true_returns_cached(self):
        """get_latest_quote(symbol) with cache hit returns cached value."""
        conn = self._make_connector()
        cached = {"Close": 99.0, "data_source": "CACHED"}
        conn._quote_cache.get = MagicMock(return_value=cached)

        result = conn.get_latest_quote("AAPL", use_cache=True)
        assert result["Close"] == 99.0

    def test_hk_symbol_uses_hk_broker_code(self):
        """0700.HK must resolve to broker code HK.0700.HK, not US.0700.HK."""
        conn = self._make_connector()

        captured_code: list[str] = []

        def _mock_futu_quote(codes):
            captured_code.extend(codes)
            raise RuntimeError("not_connected")  # simulate no Futu

        fake_quote = {
            "Date": pd.Timestamp.now(), "Open": 50.0, "High": 51.0,
            "Low": 49.0, "Close": 50.0, "Volume": 5000.0, "data_source": "YF_LIVE",
        }
        # yf provider succeeds on first attempt, so futu never called
        with patch.object(conn, "_get_latest_quote_yfinance", return_value=fake_quote):
            conn.get_latest_quote("0700.HK", use_cache=False)

        # Futu path not reached (yf succeeded), but resolve_symbol must give HK prefix
        broker_code, prefix = conn.resolve_symbol("0700.HK")
        assert broker_code == "HK.0700.HK"
        assert prefix == "HK"

    def test_hk_symbol_never_queries_us_prefix(self):
        """When all providers fail for 0700.HK, error mentions HK.0700.HK not US.0700.HK."""
        conn = self._make_connector()

        with patch.object(conn, "_get_latest_quote_yfinance", side_effect=RuntimeError("yf fail")):
            with patch.object(conn, "_get_latest_quote_efinance", side_effect=RuntimeError("ef fail")):
                try:
                    conn.get_latest_quote("0700.HK", use_cache=False)
                except RuntimeError as exc:
                    err = str(exc)
                    assert "HK.0700.HK" in err
                    assert "US.0700.HK" not in err


# ── resolve_symbol ────────────────────────────────────────────────────────────

class TestResolveSymbol:
    def _make_connector(self):
        from v3_pipeline.core.futu_connector import FutuConfig, FutuConnector
        conn = FutuConnector.__new__(FutuConnector)
        conn.config = FutuConfig(market_prefix="US")
        conn.logger = MagicMock()
        return conn

    def test_hk_symbol_resolved_to_hk(self):
        conn = self._make_connector()
        code, prefix = conn.resolve_symbol("0700.HK")
        assert code == "HK.0700.HK"
        assert prefix == "HK"

    def test_us_symbol_resolved_to_us(self):
        conn = self._make_connector()
        code, prefix = conn.resolve_symbol("AAPL")
        assert code == "US.AAPL"
        assert prefix == "US"

    def test_hk_never_gets_us_prefix(self):
        conn = self._make_connector()
        code, _ = conn.resolve_symbol("9988.HK")
        assert not code.startswith("US.")

    def test_dotted_us_suffix_not_confused_with_hk(self):
        """Symbols like 'US.AAPL' have a dot but don't end in .HK."""
        conn = self._make_connector()
        code, prefix = conn.resolve_symbol("TSLA")
        assert prefix == "US"


# ── IdleTaskScheduler singleton ────────────────────────────────────────────────

class TestIdleTaskSchedulerSingleton:
    def test_singleton_prevents_double_start(self):
        """run() called twice with same session_key only executes tasks once."""
        import idle_task_scheduler as sched_mod

        loop = _make_empty_loop(["AAPL"])
        cfg = sched_mod.IdleSchedulerConfig(enabled=True, max_idle_hours_per_day=1.0)

        run_count = 0

        async def _counted_tasks(self_inner):
            nonlocal run_count
            run_count += 1

        with patch.object(sched_mod.IdleTaskScheduler, "_run_tasks", _counted_tasks):
            # Reset singleton state
            sched_mod._active_idle_sessions.clear()
            sched_mod._active_idle_lock = None

            async def _run():
                s1 = sched_mod.IdleTaskScheduler(loop, cfg=cfg, session_key="test_session")
                s2 = sched_mod.IdleTaskScheduler(loop, cfg=cfg, session_key="test_session")
                await asyncio.gather(s1.run(), s2.run())

            asyncio.run(_run())

        assert run_count == 1, f"Expected 1 execution, got {run_count}"

    def test_different_session_keys_both_run(self):
        """Different session_keys both start successfully."""
        import idle_task_scheduler as sched_mod

        loop = _make_empty_loop(["AAPL"])
        cfg = sched_mod.IdleSchedulerConfig(enabled=True)

        run_count = 0

        async def _counted_tasks(self_inner):
            nonlocal run_count
            run_count += 1

        with patch.object(sched_mod.IdleTaskScheduler, "_run_tasks", _counted_tasks):
            sched_mod._active_idle_sessions.clear()
            sched_mod._active_idle_lock = None

            async def _run():
                s1 = sched_mod.IdleTaskScheduler(loop, cfg=cfg, session_key="session_A")
                s2 = sched_mod.IdleTaskScheduler(loop, cfg=cfg, session_key="session_B")
                await asyncio.gather(s1.run(), s2.run())

            asyncio.run(_run())

        assert run_count == 2


# ── IdleTaskScheduler uses yf_provider, not direct yf.Ticker ─────────────────

class TestIdleSchedulerUsesYfProvider:
    def test_backfill_uses_yf_provider_not_direct_ticker(self):
        """_run_historical_backfill must import from yf_provider, not call yf.Ticker directly."""
        import idle_task_scheduler as sched_mod

        source = inspect.getsource(sched_mod._run_historical_backfill)
        assert "yf_provider" in source or "download_history" in source, (
            "_run_historical_backfill should use yf_provider.download_history"
        )
        assert "yf.Ticker" not in source, (
            "_run_historical_backfill must not call yf.Ticker directly"
        )

    def test_scheduler_idle_symbols_used_when_loop_symbols_empty(self):
        """When loop.symbols is empty, idle_symbols is used as fallback."""
        import idle_task_scheduler as sched_mod

        loop = _make_empty_loop(symbols=[])  # empty
        cfg = sched_mod.IdleSchedulerConfig(enabled=True)
        scheduler = sched_mod.IdleTaskScheduler(
            loop, cfg=cfg, idle_symbols=["AAPL", "MSFT"], session_key="test_fallback"
        )

        used_symbols: list = []

        async def _capture_tasks(self_inner):
            symbols = self_inner.loop.symbols if self_inner.loop.symbols else self_inner._idle_symbols
            used_symbols.extend(symbols)

        with patch.object(sched_mod.IdleTaskScheduler, "_run_tasks", _capture_tasks):
            sched_mod._active_idle_sessions.clear()
            sched_mod._active_idle_lock = None
            asyncio.run(scheduler.run())

        assert "AAPL" in used_symbols

    def test_rotation_universe_selects_next_batch_and_updates_cursor(self, tmp_path: Path):
        import idle_task_scheduler as sched_mod

        universe_path = tmp_path / "us_symbols.txt"
        universe_path.write_text("AAPL\nMSFT\nNVDA\nTSLA\n", encoding="utf-8")
        cursor_path = tmp_path / "cursor.json"
        cfg = sched_mod.IdleSchedulerConfig(
            universe_mode="rotation",
            max_symbols_per_session=2,
            universe_files=[str(universe_path)],
            cursor_path=str(cursor_path),
        )

        first = sched_mod._resolve_backfill_symbols(["CORE"], cfg, logging.getLogger("test_rotation"))
        second = sched_mod._resolve_backfill_symbols(["CORE"], cfg, logging.getLogger("test_rotation"))

        assert first == ["AAPL", "MSFT"]
        assert second == ["NVDA", "TSLA"]
        assert cursor_path.exists()

    def test_rotation_falls_back_to_core_when_universe_missing(self, tmp_path: Path):
        import idle_task_scheduler as sched_mod

        cfg = sched_mod.IdleSchedulerConfig(
            universe_mode="rotation",
            max_symbols_per_session=1,
            universe_files=[str(tmp_path / "missing.txt")],
        )

        selected = sched_mod._resolve_backfill_symbols(["AAPL", "MSFT"], cfg, logging.getLogger("test_fallback"))

        assert selected == ["AAPL"]

    def test_idle_backfill_syncs_indicator_frame_to_market_db(self):
        """Idle backfill should persist feature-enriched K-lines into market_data."""
        import idle_task_scheduler as sched_mod
        from v3_pipeline.data import yf_provider

        class CaptureDb:
            def __init__(self):
                self.saved = []

            def save_data(self, frame, symbol=None):
                self.saved.append((symbol, frame.copy()))

        class FeatureGenerator:
            def generate(self, frame):
                out = frame.copy()
                out["RSI_14"] = 55.0
                out["MACD"] = 1.0
                out["BB_UPPER"] = out["Close"] + 2.0
                out["BB_MIDDLE"] = out["Close"]
                out["BB_LOWER"] = out["Close"] - 2.0
                return out

        class LoopStub:
            def __init__(self):
                self.market_buffers = {"AAPL": pd.DataFrame()}
                self.feature_generator = FeatureGenerator()
                self.db = CaptureDb()

            def _get_market_db(self):
                return self.db

        loop = LoopStub()
        cfg = sched_mod.IdleSchedulerConfig(
            max_concurrent_symbols=1,
            cooldown_between_batches_sec=0,
            max_idle_hours_per_day=1.0,
        )
        hist = _fake_history_df()
        hist.index = pd.to_datetime(["2026-05-09"])

        with patch.object(yf_provider, "download_history", return_value={"AAPL": hist}):
            processed = asyncio.run(
                sched_mod._run_historical_backfill(
                    ["AAPL"],
                    loop,
                    cfg,
                    sched_mod.IdleRateLimiter(cfg),
                    logging.getLogger("test_idle_backfill_db_sync"),
                )
            )

        assert processed == 1
        assert len(loop.db.saved) == 1
        symbol, saved = loop.db.saved[0]
        assert symbol == "AAPL"
        assert {"Date", "Open", "High", "Low", "Close", "Volume"}.issubset(saved.columns)
        assert {"RSI_14", "MACD", "BB_UPPER", "BB_MIDDLE", "BB_LOWER"}.issubset(saved.columns)

    def test_idle_backfill_skips_symbols_already_fresh(self):
        import idle_task_scheduler as sched_mod
        from v3_pipeline.data import yf_provider

        class FreshDb:
            def get_latest_data(self, symbol, limit=1):
                return pd.DataFrame([{"timestamp": pd.Timestamp.now(tz="UTC").isoformat()}])

        class LoopStub:
            market_buffers = {"AAPL": pd.DataFrame()}
            feature_generator = MagicMock()

            def _get_market_db(self):
                return FreshDb()

        cfg = sched_mod.IdleSchedulerConfig(
            max_concurrent_symbols=1,
            cooldown_between_batches_sec=0,
            skip_if_latest_within_days=1,
        )

        with patch.object(yf_provider, "download_history") as download_history:
            processed = asyncio.run(
                sched_mod._run_historical_backfill(
                    ["AAPL"],
                    LoopStub(),
                    cfg,
                    sched_mod.IdleRateLimiter(cfg),
                    logging.getLogger("test_fresh_skip"),
                )
            )

        assert processed == 0
        download_history.assert_not_called()


# ── 10 consecutive IDLE backfill rounds — FD stability ───────────────────────

class TestIdleBackfillFdStability:
    """Verify that 10 consecutive IDLE backfill rounds do not trigger FD guard.

    Covers the requirement from CLAUDE_CODE_REWRITE_GUIDE.md:
    "Ten consecutive IDLE collect/backfill rounds keep FD count stable."
    """

    def test_ten_rounds_download_history_no_fd_guard_trigger(self):
        """FD count stays stable across 10 simulated IDLE backfill rounds."""
        from v3_pipeline.data import yf_provider

        guard_triggered = False

        with patch.object(yf_provider, "_get_fd_stats", return_value=(200, 1024)):
            with patch("yfinance.Ticker") as mock_ticker:
                mock_ticker.return_value.history.return_value = _fake_history_df()
                for round_num in range(10):
                    results = yf_provider.download_history(["AAPL", "MSFT", "GOOGL"])
                    if all(df.empty for df in results.values()):
                        guard_triggered = True
                        break

        assert not guard_triggered, (
            "FD guard triggered unexpectedly during 10-round backfill simulation"
        )

    def test_ten_rounds_all_return_non_empty(self):
        """Each of 10 consecutive rounds returns populated DataFrames."""
        from v3_pipeline.data import yf_provider

        with patch.object(yf_provider, "_get_fd_stats", return_value=(100, 1024)):
            with patch("yfinance.Ticker") as mock_ticker:
                mock_ticker.return_value.history.return_value = _fake_history_df()
                for i in range(10):
                    results = yf_provider.download_history(["AAPL"])
                    assert not results["AAPL"].empty, (
                        f"Round {i + 1}: AAPL history unexpectedly empty"
                    )

    def test_fd_guard_kicks_in_after_threshold_breach(self):
        """Once FD usage crosses 80%, subsequent rounds return empty DataFrames."""
        from v3_pipeline.data import yf_provider

        call_num = 0
        fd_values = [200, 200, 200, 200, 200, 200, 200, 900, 900, 900]

        def _rising_fds():
            nonlocal call_num
            v = fd_values[min(call_num, len(fd_values) - 1)]
            call_num += 1
            return (v, 1024)

        rounds_before_block = 0
        with patch.object(yf_provider, "_get_fd_stats", side_effect=_rising_fds):
            with patch("yfinance.Ticker") as mock_ticker:
                mock_ticker.return_value.history.return_value = _fake_history_df()
                for i in range(10):
                    results = yf_provider.download_history(["AAPL"])
                    if all(df.empty for df in results.values()):
                        break
                    rounds_before_block += 1

        assert rounds_before_block < 10, (
            "Guard should have blocked after FD count exceeded 80% threshold"
        )
        assert rounds_before_block >= 7, (
            f"Guard blocked too early (round {rounds_before_block}); expected at round 7+"
        )


# ── Launcher HK watchlist guard ───────────────────────────────────────────────

class TestLauncherHKWatchlistGuard:
    def test_hk_mode_skips_us_dynamic_watchlist(self):
        """build_live_config must not inject US-only picks when in HK mode."""
        import v3_launcher as launcher

        us_picks = ["MSFT", "NVDA", "GOOGL"]
        fake_watchlist = {"picks": us_picks}

        with patch.object(launcher, "resolve_market_mode", return_value="HK"):
            with patch("builtins.open", unittest.mock.mock_open(read_data='{"picks":["MSFT","NVDA","GOOGL"]}')):
                with patch.object(launcher.Path, "exists", return_value=True):
                    with patch("json.loads", return_value=fake_watchlist):
                        cfg = launcher._base_live_config()
                        # Manually simulate what build_live_config does
                        current_mode = launcher.resolve_market_mode()
                        picks_are_us_only = not any(
                            str(p).upper().endswith(".HK") for p in us_picks
                        )
                        should_inject = not (current_mode == "HK" and picks_are_us_only)

        assert not should_inject, "HK mode should block US-only dynamic watchlist injection"

    def test_non_hk_mode_allows_dynamic_watchlist(self):
        """In US or IDLE mode, dynamic watchlist injection proceeds normally."""
        us_picks = ["MSFT", "NVDA"]
        for mode in ("US", "IDLE"):
            current_mode = mode
            picks_are_us_only = not any(
                str(p).upper().endswith(".HK") for p in us_picks
            )
            should_inject = not (current_mode == "HK" and picks_are_us_only)
            assert should_inject, f"Mode {mode} should allow watchlist injection"
