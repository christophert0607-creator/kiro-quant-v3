"""Tests for v3_pipeline/data/kline_backfill.py"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from v3_pipeline.data.kline_backfill import (
    MIN_BARS,
    KlineBackfill,
    _prepare_ohlcv,
    _to_db_frame,
    _to_utc_naive_str,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_hist(n: int = 80, tz_aware: bool = True) -> pd.DataFrame:
    """Create a minimal ticker.history()-style DataFrame."""
    import numpy as np

    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    if tz_aware:
        idx = idx.tz_localize("America/New_York")
    df = pd.DataFrame(
        {
            "Open": np.linspace(100, 110, n),
            "High": np.linspace(101, 111, n),
            "Low": np.linspace(99, 109, n),
            "Close": np.linspace(100.5, 110.5, n),
            "Volume": [1_000_000] * n,
            "Dividends": [0.0] * n,
            "Stock Splits": [0.0] * n,
        },
        index=idx,
    )
    df.index.name = "Date"
    return df


def _make_db(path: str, symbol: str = "AAPL", bars: int = 0) -> None:
    """Bootstrap a minimal kiro_quant.db at path with optional pre-filled rows."""
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS market_data (
            symbol TEXT, timestamp DATETIME,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            data_source TEXT,
            PRIMARY KEY (symbol, timestamp))"""
    )
    if bars:
        rows = [(symbol, f"2024-01-{i+1:02d} 00:00:00", 100.0, 101.0, 99.0, 100.5, 1e6, "YF_HIST")
                for i in range(bars)]
        conn.executemany(
            "INSERT OR REPLACE INTO market_data VALUES (?,?,?,?,?,?,?,?)", rows
        )
    conn.commit()
    conn.close()


# ── _to_utc_naive_str ─────────────────────────────────────────────────────────

class TestToUtcNaiveStr:
    def test_aware_pd_timestamp(self):
        ts = pd.Timestamp("2026-03-17 15:59:00", tz="America/New_York")
        result = _to_utc_naive_str(ts)
        # UTC would be 19:59:00
        assert "+" not in result and "Z" not in result
        assert result.startswith("2026-03-17")

    def test_naive_pd_timestamp(self):
        ts = pd.Timestamp("2026-03-17 09:30:00")
        assert _to_utc_naive_str(ts) == "2026-03-17 09:30:00"

    def test_aware_datetime(self):
        ts = datetime(2026, 3, 17, 15, 59, 0, tzinfo=timezone.utc)
        result = _to_utc_naive_str(ts)
        assert result == "2026-03-17 15:59:00"

    def test_string_with_offset(self):
        result = _to_utc_naive_str("2026-03-17 15:59:00-04:00")
        assert "+" not in result and result.count("-") <= 2
        assert result.startswith("2026-03-17 15:59:00")

    def test_string_no_offset(self):
        assert _to_utc_naive_str("2026-03-16 00:00:00") == "2026-03-16 00:00:00"


# ── _prepare_ohlcv ────────────────────────────────────────────────────────────

class TestPrepareOhlcv:
    def test_returns_required_columns(self):
        hist = _make_hist(20)
        df = _prepare_ohlcv(hist)
        for col in ("Date", "Open", "High", "Low", "Close", "Volume"):
            assert col in df.columns, f"missing {col}"

    def test_strips_extra_columns(self):
        hist = _make_hist(20)
        df = _prepare_ohlcv(hist)
        assert "Dividends" not in df.columns
        assert "Stock Splits" not in df.columns

    def test_timezone_stripped(self):
        hist = _make_hist(20, tz_aware=True)
        df = _prepare_ohlcv(hist)
        for val in df["Date"]:
            assert "+" not in str(val) and "-0" not in str(val)[-6:]

    def test_empty_input_returns_empty(self):
        result = _prepare_ohlcv(pd.DataFrame())
        assert result.empty


# ── _to_db_frame ──────────────────────────────────────────────────────────────

class TestToDbFrame:
    def test_renames_and_adds_metadata(self):
        hist = _make_hist(10)
        ohlcv = _prepare_ohlcv(hist)
        db_frame = _to_db_frame(ohlcv, "TSLA", "YF_HIST")
        assert "timestamp" in db_frame.columns
        assert db_frame["symbol"].iloc[0] == "TSLA"
        assert db_frame["data_source"].iloc[0] == "YF_HIST"
        assert "Date" not in db_frame.columns

    def test_data_source_live(self):
        hist = _make_hist(5)
        ohlcv = _prepare_ohlcv(hist)
        db_frame = _to_db_frame(ohlcv, "AAPL", "YF_LIVE")
        assert (db_frame["data_source"] == "YF_LIVE").all()


# ── KlineBackfill ─────────────────────────────────────────────────────────────

class TestKlineBackfill:
    def _make_kb(self, tmp_path: Path, prefill_sym: str = "", bars: int = 0) -> KlineBackfill:
        db_file = str(tmp_path / "test.db")
        _make_db(db_file, prefill_sym, bars)
        kb = KlineBackfill(db_path=db_file, cooldown_sec=0.0)
        return kb

    def test_symbols_needing_backfill_empty_db(self, tmp_path):
        kb = self._make_kb(tmp_path)
        result = kb.symbols_needing_backfill(["AAPL", "MSFT"])
        assert set(result) == {"AAPL", "MSFT"}

    def test_symbols_needing_backfill_sufficient(self, tmp_path):
        kb = self._make_kb(tmp_path, "AAPL", MIN_BARS)
        result = kb.symbols_needing_backfill(["AAPL"])
        assert result == []

    def test_symbols_needing_backfill_insufficient(self, tmp_path):
        kb = self._make_kb(tmp_path, "AAPL", MIN_BARS - 1)
        result = kb.symbols_needing_backfill(["AAPL"])
        assert result == ["AAPL"]

    @patch("v3_pipeline.data.kline_backfill.KlineBackfill._fetch_and_save_parallel")
    @patch("v3_pipeline.data.kline_backfill.KlineBackfill._fetch_and_save_sequential")
    def test_run_smart_repair_skips_if_sufficient(self, mock_seq, mock_par, tmp_path):
        kb = self._make_kb(tmp_path, "AAPL", MIN_BARS)
        result = kb.run_smart_repair(["AAPL"])
        mock_par.assert_not_called()
        mock_seq.assert_not_called()
        assert result == {}

    @patch("v3_pipeline.data.kline_backfill.KlineBackfill._fetch_and_save_sequential", return_value={"MSFT": 500})
    def test_run_smart_repair_calls_fetch_for_missing(self, mock_seq, tmp_path):
        # 1 symbol → ≤20 threshold → routes to _sequential (parallel only for >20 symbols)
        kb = self._make_kb(tmp_path)
        result = kb.run_smart_repair(["MSFT"])
        mock_seq.assert_called_once()
        args = mock_seq.call_args[0]
        assert args[1] == "2y"
        assert args[2] == "YF_HIST"

    @patch("v3_pipeline.data.kline_backfill.KlineBackfill._fetch_and_save_sequential", return_value={"AAPL": 5})
    def test_run_incremental_always_fetches(self, mock_seq, tmp_path):
        # run_incremental always uses sequential path regardless of max_workers
        kb = self._make_kb(tmp_path, "AAPL", MIN_BARS)
        result = kb.run_incremental(["AAPL"])
        mock_seq.assert_called_once()
        args = mock_seq.call_args[0]
        assert args[1] == "5d"
        assert args[2] == "YF_LIVE"

    def test_integration_saves_to_db(self, tmp_path):
        """End-to-end: mock yf_provider, verify rows land in DB with correct data_source."""
        db_file = str(tmp_path / "test.db")
        _make_db(db_file)

        hist = _make_hist(80)

        with patch("v3_pipeline.data.yf_provider.download_history", return_value={"NVDA": hist}):
            kb = KlineBackfill(db_path=db_file, cooldown_sec=0.0)
            saved = kb.run_smart_repair(["NVDA"])

        assert saved.get("NVDA", 0) > 0

        conn = sqlite3.connect(db_file)
        rows = conn.execute(
            "SELECT COUNT(*), data_source FROM market_data WHERE symbol='NVDA' GROUP BY data_source"
        ).fetchall()
        conn.close()
        assert rows, "no rows saved for NVDA"
        count, source = rows[0]
        assert count > 0
        assert source == "YF_HIST"

    def test_run_full_cycle_returns_both_dicts(self, tmp_path):
        kb = self._make_kb(tmp_path)
        with patch.object(kb, "run_smart_repair", return_value={"A": 500}) as m1, \
             patch.object(kb, "run_incremental", return_value={"A": 5}) as m2:
            repair, incremental = kb.run_full_cycle(["A"])
        m1.assert_called_once_with(["A"])
        m2.assert_called_once_with(["A"])
        assert repair == {"A": 500}
        assert incremental == {"A": 5}
