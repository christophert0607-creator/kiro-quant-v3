"""
Regression tests for db_manager.DatabaseManager — focused on the FD-leak
that caused 275 live FDs to kiro_quant.db after 11min of KlineBackfill
under PR #85's 300+ symbol universe (2026-05-16 production observation).

Root cause: ``with sqlite3.connect(path) as conn`` is *transaction-managed*
in Python — it commits/rolls back but does NOT close(). The connection
sits open until garbage-collected, which under ThreadPoolExecutor never
catches up. Fix is ``with closing(sqlite3.connect(...)) as conn, conn:``.
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest

# Make repo root importable so we can import db_manager
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from db_manager import DatabaseManager  # noqa: E402


def _bars(n: int = 3, symbol: str = "AAPL") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": pd.date_range("2026-05-16 09:30", periods=n, freq="min"),
            "Open": [100.0 + i for i in range(n)],
            "High": [101.0 + i for i in range(n)],
            "Low": [99.0 + i for i in range(n)],
            "Close": [100.5 + i for i in range(n)],
            "Volume": [1_000_000] * n,
            "symbol": [symbol] * n,
        }
    )


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_kiro.db")


# ── Functional ────────────────────────────────────────────────────────────────


def test_init_creates_market_data_table(db_path):
    DatabaseManager(db_path)
    import sqlite3
    with sqlite3.connect(db_path) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "market_data" in tables


def test_save_data_inserts_rows(db_path):
    db = DatabaseManager(db_path)
    db.save_data(_bars(n=4))
    import sqlite3
    with sqlite3.connect(db_path) as c:
        n = c.execute("SELECT COUNT(*) FROM market_data WHERE symbol='AAPL'").fetchone()[0]
    assert n == 4


def test_save_data_upserts_on_duplicate_key(db_path):
    db = DatabaseManager(db_path)
    db.save_data(_bars(n=2))
    db.save_data(_bars(n=2))  # same timestamps
    import sqlite3
    with sqlite3.connect(db_path) as c:
        n = c.execute("SELECT COUNT(*) FROM market_data WHERE symbol='AAPL'").fetchone()[0]
    assert n == 2  # REPLACE, not append


def test_save_data_adds_missing_columns(db_path):
    db = DatabaseManager(db_path)
    df = _bars()
    df["RSI_14"] = 55.5
    db.save_data(df)
    import sqlite3
    with sqlite3.connect(db_path) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(market_data)")}
    assert "RSI_14" in cols


def test_check_health_reports_per_symbol(db_path):
    db = DatabaseManager(db_path)
    db.save_data(_bars(n=3, symbol="AAPL"))
    db.save_data(_bars(n=5, symbol="MSFT"))
    report = db.check_health(["AAPL", "MSFT", "NVDA"])
    assert report["AAPL"]["count"] == 3
    assert report["MSFT"]["count"] == 5
    assert report["NVDA"]["count"] == 0


# ── FD leak regression ──────────────────────────────────────────────────────


def _open_fds_to(path: str) -> int:
    """Count FDs that the current process holds open against ``path``."""
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.exists():
        pytest.skip("/proc/self/fd not available on this platform")
    n = 0
    for fd in fd_dir.iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue  # FD vanished between listing and readlink
        # Match either exact path or "<path> (deleted)" form
        if target == path or target == f"{path} (deleted)":
            n += 1
    return n


def test_save_data_does_not_leak_fds(db_path):
    """100 sequential save_data calls must not leave 100 FDs open to the db."""
    db = DatabaseManager(db_path)
    baseline = _open_fds_to(db_path)
    for i in range(100):
        df = _bars(n=1, symbol=f"SYM{i:03d}")
        db.save_data(df)
    after = _open_fds_to(db_path)
    # Allow ≤2 transient FDs (sqlite occasionally holds a wal companion)
    assert after - baseline <= 2, (
        f"FD leak: started with {baseline}, ended with {after} "
        f"after 100 save_data calls"
    )


def test_parallel_save_data_does_not_leak_fds(db_path):
    """ThreadPoolExecutor save_data x 60 must not leak 60+ FDs (production regression)."""
    db = DatabaseManager(db_path)
    baseline = _open_fds_to(db_path)

    def _do(i: int):
        df = _bars(n=2, symbol=f"PAR{i:03d}")
        db.save_data(df)

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(_do, range(60)))
    after = _open_fds_to(db_path)
    # Under PR #85's 4-worker backfill this leaked 275 FDs in 11min; expect ≤5 now
    assert after - baseline <= 5, (
        f"Parallel FD leak: baseline={baseline} after={after} "
        f"(was 275 in production with raw `with sqlite3.connect()`)"
    )


def test_get_latest_data_does_not_leak_fds(db_path):
    db = DatabaseManager(db_path)
    db.save_data(_bars(n=10))
    baseline = _open_fds_to(db_path)
    for _ in range(50):
        _ = db.get_latest_data("AAPL", limit=10)
    after = _open_fds_to(db_path)
    assert after - baseline <= 2


def test_check_health_does_not_leak_fds(db_path):
    db = DatabaseManager(db_path)
    db.save_data(_bars(n=3))
    baseline = _open_fds_to(db_path)
    for _ in range(50):
        _ = db.check_health(["AAPL", "MSFT"])
    after = _open_fds_to(db_path)
    assert after - baseline <= 2
