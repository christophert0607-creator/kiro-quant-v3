"""Tests for v3_pipeline/data/db_persist.py (PR #83)"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from v3_pipeline.data.db_persist import LiveDbPersist, PersistConfig, _PendingRow


# -- Helpers --


def _make_df(n: int = 5, symbol: str = "AAPL") -> pd.DataFrame:
    """Create a minimal market-buffer-style DataFrame."""
    idx = pd.date_range("2026-05-16 09:30", periods=n, freq="min")
    df = pd.DataFrame(
        {
            "Date": idx,
            "Open": [100.0 + i for i in range(n)],
            "High": [101.0 + i for i in range(n)],
            "Low": [99.0 + i for i in range(n)],
            "Close": [100.5 + i for i in range(n)],
            "Volume": [1_000_000] * n,
            "data_source": ["live"] * n,
        }
    )
    return df


def _run_with_sleep(coro_fn, sleep_sec: float = 0.3):
    """Run an async coroutine, keep loop alive for sleep_sec so background tasks run, then return result."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro_fn)
        if sleep_sec > 0:
            # Run the event loop a bit more so background writer can flush
            loop.run_until_complete(asyncio.sleep(sleep_sec))
        return result
    finally:
        loop.close()
        asyncio.set_event_loop(None)


# -- LiveDbPersist --


@pytest.fixture
def tmp_db():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td) / "test_kiro.db"


class TestInit:
    def test_wal_enabled(self, tmp_db: Path):
        p = LiveDbPersist(str(tmp_db))
        conn = sqlite3.connect(str(tmp_db))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode.upper() == "WAL"
        asyncio.run(p.close())

    def test_tables_created(self, tmp_db: Path):
        p = LiveDbPersist(str(tmp_db))
        conn = sqlite3.connect(str(tmp_db))
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        assert "market_data" in tables
        assert "market_data_archive" in tables
        asyncio.run(p.close())


class TestEnqueueAndFlush:
    def test_enqueue_writes_rows(self, tmp_db: Path):
        cfg = PersistConfig(db_path=str(tmp_db), batch_size=2, flush_interval_sec=0.1)
        p = LiveDbPersist(cfg=cfg)

        async def _body():
            await p.start()
            df = _make_df(n=4, symbol="TSLA")
            await p.enqueue("TSLA", df)
            await asyncio.sleep(0.3)
            await p.close()
        asyncio.run(_body())

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT count(*) FROM market_data WHERE symbol='TSLA'").fetchone()[0]
        conn.close()
        assert rows == 4

    def test_enqueue_multiple_symbols(self, tmp_db: Path):
        cfg = PersistConfig(db_path=str(tmp_db), batch_size=5, flush_interval_sec=0.1)
        p = LiveDbPersist(cfg=cfg)

        async def _body():
            await p.start()
            for sym in ["AAPL", "MSFT", "NVDA"]:
                await p.enqueue(sym, _make_df(n=3, symbol=sym))
            await asyncio.sleep(0.3)
            await p.close()
        asyncio.run(_body())

        conn = sqlite3.connect(str(tmp_db))
        counts = {r[0]: r[1] for r in conn.execute("SELECT symbol, count(*) FROM market_data GROUP BY symbol")}
        conn.close()
        assert counts == {"AAPL": 3, "MSFT": 3, "NVDA": 3}

    def test_upsert_replaces_duplicate_key(self, tmp_db: Path):
        cfg = PersistConfig(db_path=str(tmp_db), batch_size=2, flush_interval_sec=0.1)
        p = LiveDbPersist(cfg=cfg)

        async def _body():
            await p.start()
            df1 = _make_df(n=2, symbol="AAPL")
            await p.enqueue("AAPL", df1)
            await asyncio.sleep(0.2)
            df2 = df1.copy()
            df2["Close"] = df2["Close"] + 10.0
            await p.enqueue("AAPL", df2)
            await asyncio.sleep(0.2)
            await p.close()
        asyncio.run(_body())

        conn = sqlite3.connect(str(tmp_db))
        rows = list(conn.execute("SELECT timestamp, close FROM market_data WHERE symbol='AAPL' ORDER BY timestamp"))
        conn.close()
        assert len(rows) == 2
        assert rows[0][1] == pytest.approx(100.5 + 10.0)

    def test_empty_df_noop(self, tmp_db: Path):
        cfg = PersistConfig(db_path=str(tmp_db), batch_size=2, flush_interval_sec=0.1)
        p = LiveDbPersist(cfg=cfg)

        async def _body():
            await p.start()
            await p.enqueue("AAPL", pd.DataFrame())
            await asyncio.sleep(0.2)
            await p.close()
        asyncio.run(_body())

        assert p.stats()["enqueued"] == 0


class TestQueueLimits:
    def test_drops_when_queue_full(self, tmp_db: Path):
        cfg = PersistConfig(db_path=str(tmp_db), batch_size=100, flush_interval_sec=60.0, max_queue_size=5)
        p = LiveDbPersist(cfg=cfg)

        async def _body():
            await p.start()
            df = _make_df(n=10, symbol="AAPL")
            await p.enqueue("AAPL", df)
            await asyncio.sleep(0.05)
            await p.close()
        asyncio.run(_body())

        stats = p.stats()
        assert stats["enqueued"] == 5
        assert stats["dropped"] == 5


class TestArchive:
    def test_archive_moves_old_rows(self, tmp_db: Path):
        cfg = PersistConfig(db_path=str(tmp_db), batch_size=10, flush_interval_sec=0.1, archive_after_days=0)
        p = LiveDbPersist(cfg=cfg)

        archived_result = []

        async def _body():
            await p.start()
            df = _make_df(n=5, symbol="AAPL")
            df["Date"] = pd.date_range("2026-05-15 09:30", periods=5, freq="min")
            await p.enqueue("AAPL", df)
            await asyncio.sleep(0.3)
            # Archive while connection is still open, before close()
            archived_result.append(await p.archive_old_data())
            await p.close()
        asyncio.run(_body())

        conn = sqlite3.connect(str(tmp_db))
        live_rows = conn.execute("SELECT count(*) FROM market_data WHERE symbol='AAPL'").fetchone()[0]
        archive_rows = conn.execute("SELECT count(*) FROM market_data_archive WHERE symbol='AAPL'").fetchone()[0]
        conn.close()

        assert archived_result[0] == 5
        assert live_rows == 0
        assert archive_rows == 5


class TestStats:
    def test_stats_reflect_progress(self, tmp_db: Path):
        cfg = PersistConfig(db_path=str(tmp_db), batch_size=2, flush_interval_sec=0.1)
        p = LiveDbPersist(cfg=cfg)

        async def _body():
            await p.start()
            await p.enqueue("AAPL", _make_df(n=4))
            await asyncio.sleep(0.3)
            await p.close()
        asyncio.run(_body())

        stats = p.stats()
        assert stats["enqueued"] == 4
        assert stats["written"] == 4
        assert stats["queue_size"] == 0


# -- Graceful shutdown --


class TestClose:
    def test_flush_on_close(self, tmp_db: Path):
        cfg = PersistConfig(db_path=str(tmp_db), batch_size=100, flush_interval_sec=60.0)
        p = LiveDbPersist(cfg=cfg)

        async def _body():
            await p.start()
            await p.enqueue("AAPL", _make_df(n=3))
            # Immediately close -- should flush pending rows
            await p.close()
        asyncio.run(_body())

        conn = sqlite3.connect(str(tmp_db))
        rows = conn.execute("SELECT count(*) FROM market_data").fetchone()[0]
        conn.close()
        assert rows == 3

    def test_idempotent_close(self, tmp_db: Path):
        p = LiveDbPersist(str(tmp_db))
        asyncio.run(p.start())
        asyncio.run(p.close())
        # Second close should be harmless
        asyncio.run(p.close())
