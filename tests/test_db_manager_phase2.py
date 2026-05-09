from pathlib import Path
import sqlite3

from db_manager import DatabaseManager


def test_phase2_tables_exist(tmp_path: Path):
    db_path = tmp_path / "phase2.db"
    DatabaseManager(str(db_path))

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}

    assert "market_data" in tables
    assert "executions" in tables
    assert "position_snapshots" in tables
    assert "pnl_snapshots" in tables
    assert "risk_events" in tables
    assert "alerts" in tables


def test_phase2_record_writes(tmp_path: Path):
    db_path = tmp_path / "phase2.db"
    db = DatabaseManager(str(db_path))

    db.save_execution(
        symbol="0700.HK",
        side="BUY",
        qty=10,
        requested_price=100.0,
        fill_price=100.5,
        reason="unit_test",
        mode="paper_trade",
        metadata={"foo": "bar"},
    )
    db.save_position_snapshot(symbol="0700.HK", qty=10, avg_cost=100.5, source="paper_trade", reason="unit_test")
    db.save_pnl_snapshot(cash=9000.0, equity=10005.0, realized_pnl=0.0, unrealized_pnl=5.0, net_pnl=5.0, fill_count=1)
    db.save_risk_event(event_code="TEST_EVENT", message="hello", severity="WARN", symbol="0700.HK")
    db.save_alert(channel="telegram", message="test alert")

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select count(*) from executions").fetchone()[0] == 1
        assert conn.execute("select count(*) from position_snapshots").fetchone()[0] == 1
        assert conn.execute("select count(*) from pnl_snapshots").fetchone()[0] == 1
        assert conn.execute("select count(*) from risk_events").fetchone()[0] == 1
        assert conn.execute("select count(*) from alerts").fetchone()[0] == 1


def test_save_market_quote_upserts_snapshot(tmp_path: Path):
    db_path = tmp_path / "quotes.db"
    db = DatabaseManager(str(db_path))

    quote = {
        "Date": "2026-05-09T10:00:00+00:00",
        "Open": 100.0,
        "High": 101.0,
        "Low": 99.0,
        "Close": 100.5,
        "Volume": 12345,
        "data_source": "unit",
    }

    db.save_market_quote("US.TSLA", quote)
    db.save_market_quote("US.TSLA", {**quote, "Close": 101.5})

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "select symbol, timestamp, open, high, low, close, volume, source from market_data"
        ).fetchall()

    assert rows == [("US.TSLA", "2026-05-09T10:00:00+00:00", 100.0, 101.0, 99.0, 101.5, 12345.0, "unit")]
