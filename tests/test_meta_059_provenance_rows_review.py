import importlib.util
import json
import sqlite3
from pathlib import Path

_REVIEW_PATH = Path(__file__).resolve().parents[1] / "self_learn" / "scripts" / "meta_059_provenance_rows_review.py"
_spec = importlib.util.spec_from_file_location("meta_059_provenance_rows_review_under_test", _REVIEW_PATH)
assert _spec and _spec.loader
_review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_review)


def _create_db(path: Path, *, with_provenance_columns: bool = True) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE predictions (id INTEGER PRIMARY KEY, symbol TEXT)")
        conn.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                prediction_id INTEGER,
                action TEXT,
                status TEXT
            )
            """
        )
        if with_provenance_columns:
            conn.execute(
                """
                CREATE TABLE outcomes (
                    signal_id INTEGER,
                    pnl REAL,
                    pnl_pct REAL,
                    closed_at TEXT,
                    source TEXT,
                    broker_order_id TEXT,
                    recorded_by TEXT,
                    provenance_meta TEXT
                )
                """
            )
        else:
            conn.execute(
                """
                CREATE TABLE outcomes (
                    signal_id INTEGER,
                    pnl REAL,
                    pnl_pct REAL,
                    closed_at TEXT
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


def _insert_outcome(
    db_path: Path,
    *,
    signal_id: int,
    symbol: str,
    source: str | None,
    broker_order_id: str | None = None,
    recorded_by: str | None = None,
    provenance_meta: dict | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO predictions (id, symbol) VALUES (?, ?)", (signal_id, symbol))
        conn.execute(
            "INSERT INTO signals (id, prediction_id, action, status) VALUES (?, ?, 'BUY', 'CLOSED')",
            (signal_id, signal_id),
        )
        conn.execute(
            """
            INSERT INTO outcomes (
                signal_id, pnl, pnl_pct, closed_at, source,
                broker_order_id, recorded_by, provenance_meta
            ) VALUES (?, 1.0, 0.01, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                f"2026-06-05T00:0{signal_id}:00Z",
                source,
                broker_order_id,
                recorded_by,
                json.dumps(provenance_meta) if provenance_meta is not None else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_provenance_review_classifies_only_paper_live_rows_with_evidence_as_eligible(tmp_path):
    db_path = tmp_path / "fixture.db"
    _create_db(db_path)

    # Synthetic/legacy rows may have provenance markers from migration, but must not
    # count as durable broker evidence for meta-label enforcement.
    _insert_outcome(
        db_path,
        signal_id=1,
        symbol="AAPL",
        source="synthetic_seed",
        recorded_by="seed_synthetic_outcomes_legacy",
        provenance_meta={"migration": "legacy_seed"},
    )
    _insert_outcome(db_path, signal_id=2, symbol="MSFT", source="paper_broker", broker_order_id="PAPER-1")
    _insert_outcome(
        db_path,
        signal_id=3,
        symbol="0700.HK",
        source="live_broker",
        recorded_by="futu_connector",
        provenance_meta={"order_id": "LIVE-1"},
    )
    _insert_outcome(db_path, signal_id=4, symbol="TSLA", source="paper_broker")
    _insert_outcome(db_path, signal_id=5, symbol="NVDA", source=None, broker_order_id="ORPHAN-1")

    result = _review.review(db_path, min_eligible=2, sample_limit=10)

    assert result["ok"] is True
    assert result["live_trading_changes"] is False
    assert result["schema_ready"] is True
    assert result["total_outcomes"] == 5
    assert result["source_counts"] == {
        "paper_broker": 2,
        "NULL": 1,
        "live_broker": 1,
        "synthetic_seed": 1,
    }
    assert result["eligible_real_source_count"] == 2
    assert result["real_source_verified"] is True
    assert result["evidence_counts"]["with_broker_order_id_any_source"] == 2
    assert result["evidence_counts"]["with_provenance_meta_any_source"] == 2
    assert result["evidence_counts"]["eligible_source_with_broker_order_id"] == 1
    assert result["evidence_counts"]["eligible_source_with_provenance_meta"] == 1
    assert result["eligibility_status_counts"] == {
        "missing_source": 1,
        "real_source_missing_evidence": 1,
        "eligible_real": 2,
        "non_real_or_synthetic": 1,
    }

    statuses_by_signal_id = {row["signal_id"]: row["eligibility_status"] for row in result["sample_recent_outcomes"]}
    assert statuses_by_signal_id[1] == "non_real_or_synthetic"
    assert statuses_by_signal_id[2] == "eligible_real"
    assert statuses_by_signal_id[3] == "eligible_real"
    assert statuses_by_signal_id[4] == "real_source_missing_evidence"
    assert statuses_by_signal_id[5] == "missing_source"


def test_provenance_review_keeps_schema_not_ready_databases_blocked(tmp_path):
    db_path = tmp_path / "legacy.db"
    _create_db(db_path, with_provenance_columns=False)

    result = _review.review(db_path, min_eligible=1, sample_limit=3)

    assert result["ok"] is True
    assert result["live_trading_changes"] is False
    assert result["schema_ready"] is False
    assert result["missing_columns"] == ["broker_order_id", "provenance_meta", "recorded_by", "source"]
    assert result["eligible_real_source_count"] == 0
    assert result["real_source_verified"] is False
    assert "disabled" in result["recommendation"]
