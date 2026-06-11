import importlib.util
import json
import sqlite3
from pathlib import Path

_CHECK_PATH = Path(__file__).resolve().parents[1] / "self_learn" / "scripts" / "meta_061_shadow_provenance_safety_check.py"
_spec = importlib.util.spec_from_file_location("meta_061_shadow_provenance_safety_check_under_test", _CHECK_PATH)
assert _spec and _spec.loader
_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_check)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _create_db(path: Path, *, eligible_rows: int = 0, synthetic_rows: int = 0) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE predictions (id INTEGER PRIMARY KEY, symbol TEXT)")
        conn.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, prediction_id INTEGER, action TEXT, status TEXT)")
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
        signal_id = 1
        for _ in range(eligible_rows):
            conn.execute("INSERT INTO predictions (id, symbol) VALUES (?, 'AAPL')", (signal_id,))
            conn.execute("INSERT INTO signals (id, prediction_id, action, status) VALUES (?, ?, 'BUY', 'CLOSED')", (signal_id, signal_id))
            conn.execute(
                "INSERT INTO outcomes VALUES (?, 1.0, 0.01, '2026-06-05T00:00:00Z', 'paper_broker', ?, 'fixture', NULL)",
                (signal_id, f"PAPER-{signal_id}"),
            )
            signal_id += 1
        for _ in range(synthetic_rows):
            conn.execute("INSERT INTO predictions (id, symbol) VALUES (?, 'MSFT')", (signal_id,))
            conn.execute("INSERT INTO signals (id, prediction_id, action, status) VALUES (?, ?, 'BUY', 'CLOSED')", (signal_id, signal_id))
            conn.execute(
                "INSERT INTO outcomes VALUES (?, 1.0, 0.01, '2026-06-05T00:00:00Z', 'synthetic_seed', NULL, 'seed_synthetic_outcomes_legacy', ?)",
                (signal_id, json.dumps({"migration": "legacy_seed"})),
            )
            signal_id += 1
        conn.commit()
    finally:
        conn.close()


def test_safety_check_blocks_when_meta_source_is_not_ok_and_real_provenance_is_insufficient(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    db_path = tmp_path / "trading_bot.db"
    _write_jsonl(
        log_path,
        [
            {"event": "trade_quality_gate", "ts": "2026-06-05T00:00:00+00:00", "symbol": "AAPL", "shadow": True, "decision": "PASS", "score": 0.72},
            {"event": "meta_label_gate", "ts": "2026-06-05T00:00:01+00:00", "symbol": "AAPL", "shadow": True, "decision": "NO_DATA", "source_ok": False, "reason": "insufficient_real_outcomes"},
        ],
    )
    _create_db(db_path, eligible_rows=0, synthetic_rows=2)

    result = _check.build_safety_check(log_path=log_path, db_path=db_path, days=0, min_eligible_outcomes=1)

    assert result["ok"] is True
    assert result["live_trading_changes"] is False
    assert result["enforcement_safe"] is False
    assert result["recommendation"] == "keep_meta_label_enforcement_disabled"
    assert "insufficient_eligible_real_outcomes:0/1" in result["blockers"]
    assert "meta_label_source_not_ok_events:1" in result["blockers"]
    assert "meta_label_no_data_events:1" in result["warnings"]


def test_safety_check_allows_consideration_only_for_shadow_meta_with_verified_provenance(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    db_path = tmp_path / "trading_bot.db"
    _write_jsonl(
        log_path,
        [
            {"event": "trade_quality_gate", "ts": "2026-06-05T00:00:00+00:00", "symbol": "0700.HK", "shadow": True, "decision": "PASS", "score": 0.8},
            {"event": "meta_label_gate", "ts": "2026-06-05T00:00:01+00:00", "symbol": "0700.HK", "shadow": True, "decision": "CONFIRM", "source_ok": True},
        ],
    )
    _create_db(db_path, eligible_rows=2, synthetic_rows=1)

    result = _check.build_safety_check(log_path=log_path, db_path=db_path, days=0, min_eligible_outcomes=2)

    assert result["enforcement_safe"] is True
    assert result["blockers"] == []
    assert result["provenance_summary"]["eligible_real_source_count"] == 2
    assert result["shadow_gate_summary"]["gates"]["meta_label_gate"]["source_ok_counts"] == {"true": 1}
