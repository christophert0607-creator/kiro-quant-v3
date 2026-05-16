"""
db_persist.py — High-frequency SQLite persistence for live trading market data.

Improvements over db_manager.py:
  • Persistent connection (no open/close per write)
  • WAL mode for better read/write concurrency
  • Async queue + background worker to avoid blocking the main loop
  • Batch UPSERT (INSERT OR REPLACE) with configurable batch size & flush interval
  • Graceful shutdown with pending flush

Usage in LiveTradingLoop:
    from v3_pipeline.data.db_persist import LiveDbPersist
    self.db_persist = LiveDbPersist("kiro_quant.db")
    # At end of each cycle:
    await self.db_persist.enqueue(symbol, buffer_df)
    # On shutdown:
    await self.db_persist.close()
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("kiro.db_persist")

# Sentinel for background worker shutdown
_POISON = object()


@dataclass
class PersistConfig:
    db_path: str = "kiro_quant.db"
    batch_size: int = 1000         # Max rows per SQLite executemany batch (up from 500)
    max_queue_size: int = 50000    # Rows; oldest dropped if exceeded to protect memory
    flush_interval_sec: float = 60.0  # Force flush every 60s (up from 5s for 300 symbols)
    wal_checkpoint_pages: int = 1000  # PRAGMA wal_autocheckpoint
    archive_after_days: int = 14      # Move rows older than 14 days to archive table (more aggressive)


@dataclass
class _PendingRow:
    symbol: str
    timestamp: str
    open_: float
    high: float
    low: float
    close: float
    volume: float
    data_source: str = "live"


class LiveDbPersist:
    """
    Async SQLite persistence layer optimised for high-frequency live trading.

    Features
    --------
    • Background writer coroutine drains an asyncio.Queue and commits in batches.
    • Persistent sqlite3.Connection with WAL + synchronous=NORMAL for throughput.
    • Automatic schema migration (adds missing columns dynamically).
    • Optional idle-time archive that moves old rows to ``market_data_archive``.
    """

    def __init__(self, db_path: Optional[str] = None, cfg: Optional[PersistConfig] = None):
        self.cfg = cfg or PersistConfig()
        if db_path:
            self.cfg.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._conn_inode: Optional[int] = None  # inode the live conn was opened against
        self._reconnect_count: int = 0
        self._queue: asyncio.Queue[_PendingRow | None] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._closed = False
        self._flush_event = asyncio.Event()
        self._total_enqueued = 0
        self._total_written = 0
        self._dropped = 0
        self._ensure_db()

    # ── Public API ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch the background writer coroutine."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._writer_loop())
            logger.info("[db_persist] writer started (db=%s)", self.cfg.db_path)

    async def enqueue(self, symbol: str, df: pd.DataFrame) -> None:
        """Queue a DataFrame of market bars for asynchronous persistence."""
        if self._closed or df.empty:
            return
        # Drop oldest rows if queue is over limit to avoid unbounded memory growth
        qsize = self._queue.qsize()
        rows_to_add = len(df)
        if qsize + rows_to_add > self.cfg.max_queue_size:
            excess = qsize + rows_to_add - self.cfg.max_queue_size
            self._dropped += excess
            logger.warning(
                "[db_persist] queue limit exceeded — dropping %d rows (symbol=%s)",
                excess,
                symbol,
            )
            if excess >= rows_to_add:
                return
            df = df.iloc[-(rows_to_add - excess) :].copy()

        for _, row in df.iterrows():
            ts = row.get("Date", row.get("timestamp", pd.Timestamp.now(tz="UTC")))
            if isinstance(ts, pd.Timestamp):
                ts = ts.strftime("%Y-%m-%d %H:%M:%S")
            pending = _PendingRow(
                symbol=symbol,
                timestamp=ts,
                open_=float(row.get("Open", 0.0) or 0.0),
                high=float(row.get("High", 0.0) or 0.0),
                low=float(row.get("Low", 0.0) or 0.0),
                close=float(row.get("Close", 0.0) or 0.0),
                volume=float(row.get("Volume", 0.0) or 0.0),
                data_source=str(row.get("data_source", "live")),
            )
            await self._queue.put(pending)
            self._total_enqueued += 1

    async def flush(self) -> None:
        """Signal the writer to flush immediately and wait until drained."""
        if self._worker_task is None or self._worker_task.done():
            return
        self._flush_event.set()
        for _ in range(100):
            if self._queue.empty():
                break
            await asyncio.sleep(0.05)

    async def archive_old_data(self) -> int:
        """
        Move rows older than ``archive_after_days`` to ``market_data_archive``.
        Returns number of rows archived.
        """
        return await asyncio.to_thread(self._archive_old_data_sync)

    async def close(self) -> None:
        """Graceful shutdown: flush queue, stop worker, close connection."""
        if self._closed:
            return
        self._closed = True
        await self._queue.put(_POISON)  # Poison pill
        if self._worker_task and not self._worker_task.done():
            try:
                await asyncio.wait_for(self._worker_task, timeout=10.0)
            except asyncio.TimeoutError:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
        await asyncio.to_thread(self._close_conn)
        logger.info(
            "[db_persist] closed — enqueued=%d written=%d dropped=%d",
            self._total_enqueued,
            self._total_written,
            self._dropped,
        )

    # ── Sync helpers (run via asyncio.to_thread) ───────────────────────────────

    def _current_path_inode(self) -> Optional[int]:
        """Return the inode of the file currently at cfg.db_path, or None if absent."""
        try:
            return os.stat(self.cfg.db_path).st_ino
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("[db_persist] stat(%s) failed: %s", self.cfg.db_path, exc)
            return None

    def _db_path_changed(self) -> bool:
        """True when the on-disk path's inode no longer matches the live connection's inode.

        Indicates the DB file was deleted, replaced, or moved out from under us — in
        which case ``self._conn`` is now writing to an orphan inode and every commit
        is silently lost. Caller should reopen via ``_reconnect_if_path_changed``.
        """
        if self._conn is None or self._conn_inode is None:
            return False
        current = self._current_path_inode()
        return current is not None and current != self._conn_inode

    def _reconnect_if_path_changed(self) -> bool:
        """Detect a swapped inode and rebuild the connection.

        Returns True when a reconnect happened. Safe to call from the writer thread
        before each batch.
        """
        if not self._db_path_changed():
            return False
        self._reconnect_count += 1
        logger.error(
            "[db_persist] db_path inode changed (was=%s now=%s) — reconnecting (count=%d)",
            self._conn_inode, self._current_path_inode(), self._reconnect_count,
        )
        # Close the old conn (still pointing to orphan inode); ignore errors
        old = self._conn
        self._conn = None
        self._conn_inode = None
        try:
            if old is not None:
                old.close()
        except Exception:
            pass
        self._ensure_db()
        return True

    def _ensure_db(self) -> None:
        """Initialise DB, enable WAL, create tables if missing. Records inode."""
        self._conn = sqlite3.connect(self.cfg.db_path, check_same_thread=False)
        self._conn_inode = self._current_path_inode()
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute(f"PRAGMA wal_autocheckpoint = {self.cfg.wal_checkpoint_pages}")
        self._conn.execute("PRAGMA temp_store = MEMORY")
        self._conn.execute("PRAGMA mmap_size = 30000000000")
        self._conn.commit()

        # Main live table
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_data (
                symbol TEXT,
                timestamp DATETIME,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                data_source TEXT,
                PRIMARY KEY (symbol, timestamp)
            ) WITHOUT ROWID
            """
        )
        # Archive table (identical schema, no PK constraint for speed)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_data_archive (
                symbol TEXT,
                timestamp DATETIME,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                data_source TEXT
            )
            """
        )
        # Index for archive queries
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_archive_symbol_ts ON market_data_archive(symbol, timestamp)"
        )
        self._conn.commit()
        logger.info("[db_persist] DB initialised (WAL) at %s", self.cfg.db_path)

    def _close_conn(self) -> None:
        if self._conn:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._conn.commit()
            except Exception:
                pass
            self._conn.close()
            self._conn = None
            self._conn_inode = None

    def _batch_upsert(self, rows: list[_PendingRow]) -> int:
        """Synchronous batch write inside the background worker."""
        if not rows:
            return 0
        # Detect a swapped inode (file deleted/replaced under us) and reopen before write
        self._reconnect_if_path_changed()
        if self._conn is None:
            return 0
        sql = """
            INSERT OR REPLACE INTO market_data
            (symbol, timestamp, open, high, low, close, volume, data_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            (r.symbol, r.timestamp, r.open_, r.high, r.low, r.close, r.volume, r.data_source)
            for r in rows
        ]
        try:
            self._conn.executemany(sql, params)
            self._conn.commit()
            return len(rows)
        except sqlite3.OperationalError as exc:
            logger.error("[db_persist] batch upsert failed: %s", exc)
            return 0

    def _archive_old_data_sync(self) -> int:
        """
        Synchronous archive implementation.

        Opens its own connection so it doesn't contend with the background
        writer thread, which holds self._conn for batch writes.
        WAL mode allows the two connections to serialise cleanly.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.cfg.archive_after_days)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            conn = sqlite3.connect(self.cfg.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode = WAL")
            try:
                cur = conn.execute(
                    "INSERT INTO market_data_archive SELECT * FROM market_data WHERE timestamp < ?",
                    (cutoff,),
                )
                archived = cur.rowcount
                if archived:
                    conn.execute("DELETE FROM market_data WHERE timestamp < ?", (cutoff,))
                    conn.commit()
                    logger.info("[db_persist] archived %d rows older than %s", archived, cutoff)
                return archived
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            logger.error("[db_persist] archive failed: %s", exc)
            return 0

    # ── Background writer ───────────────────────────────────────────────────────

    async def _writer_loop(self) -> None:
        """Coroutine that drains the queue and commits in batches."""
        batch: list[_PendingRow] = []
        last_flush = asyncio.get_event_loop().time()

        while True:
            try:
                # Wait for a row, but time out at flush_interval_sec
                elapsed = asyncio.get_event_loop().time() - last_flush
                timeout = self.cfg.flush_interval_sec - elapsed
                if timeout > 0.001:
                    item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                else:
                    item = await self._queue.get()
            except asyncio.TimeoutError:
                item = None  # Force a flush check

            if item is None:
                # Timeout flush
                if batch:
                    written = await asyncio.to_thread(self._batch_upsert, batch)
                    self._total_written += written
                    batch.clear()
                    last_flush = asyncio.get_event_loop().time()
                if self._closed:
                    break
                continue

            if item is _POISON:
                if batch:
                    written = await asyncio.to_thread(self._batch_upsert, batch)
                    self._total_written += written
                    batch.clear()
                    last_flush = asyncio.get_event_loop().time()
                break

            batch.append(item)

            if len(batch) >= self.cfg.batch_size:
                written = await asyncio.to_thread(self._batch_upsert, batch)
                self._total_written += written
                batch.clear()
                last_flush = asyncio.get_event_loop().time()

            # Also handle explicit flush requests
            if self._flush_event.is_set():
                if batch:
                    written = await asyncio.to_thread(self._batch_upsert, batch)
                    self._total_written += written
                    batch.clear()
                    last_flush = asyncio.get_event_loop().time()
                self._flush_event.clear()

        # Final drain (should already be empty, but be safe)
        if batch:
            written = await asyncio.to_thread(self._batch_upsert, batch)
            self._total_written += written

    # ── Health / introspection ────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "enqueued": self._total_enqueued,
            "written": self._total_written,
            "dropped": self._dropped,
            "queue_size": self._queue.qsize(),
            "db_path": self.cfg.db_path,
        }
