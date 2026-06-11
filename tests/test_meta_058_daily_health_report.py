import argparse
import importlib.util
import json
import sqlite3
from pathlib import Path

_REPORT_PATH = Path(__file__).resolve().parents[1] / "self_learn" / "scripts" / "meta_058_daily_health_report.py"
_spec = importlib.util.spec_from_file_location("meta_058_daily_health_report_under_test", _REPORT_PATH)
assert _spec and _spec.loader
_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_report)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _create_db(path: Path, *, eligible_rows: int = 0, synthetic_rows: int = 0) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE predictions (id INTEGER PRIMARY KEY, symbol TEXT)")
        conn.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY, prediction_id INTEGER, action TEXT, status TEXT)")
        conn.execute("CREATE TABLE outcomes (signal_id INTEGER, pnl REAL, pnl_pct REAL, closed_at TEXT, source TEXT, broker_order_id TEXT, recorded_by TEXT, provenance_meta TEXT)")
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


def _args(tmp_path: Path, *, log_path: Path, db_path: Path) -> argparse.Namespace:
    training_log = tmp_path / "training_log.jsonl"
    training_log.write_text(json.dumps({"trained_at": "2026-06-05T00:00:00Z", "metrics": {"accuracy": 0.6}}) + "\n")
    return argparse.Namespace(
        days=0,
        log=log_path,
        db=db_path,
        training_log=training_log,
        min_eligible_outcomes=2,
    )


def test_daily_health_report_embeds_blocking_meta_label_safety_summary(tmp_path):
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

    result = _report.build_report(_args(tmp_path, log_path=log_path, db_path=db_path))

    safety = result["meta_label_safety_summary"]
    assert result["ok"] is True
    assert result["live_trading_changes"] is False
    assert safety["live_trading_changes"] is False
    assert safety["enforcement_safe"] is False
    assert safety["recommendation"] == "keep_meta_label_enforcement_disabled"
    assert safety["meta_label_gate_events"] == 1
    assert safety["trade_quality_gate_events"] == 1
    assert safety["eligible_real_source_count"] == 0
    assert safety["source_counts"] == {"synthetic_seed": 2}
    assert "insufficient_eligible_real_outcomes:0/2" in safety["blockers"]
    assert "meta_label_source_not_ok_events:1" in safety["blockers"]


def test_daily_health_report_marks_safety_considerable_only_with_verified_real_provenance(tmp_path):
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

    result = _report.build_report(_args(tmp_path, log_path=log_path, db_path=db_path))

    safety = result["meta_label_safety_summary"]
    assert safety["enforcement_safe"] is True
    assert safety["blockers"] == []
    assert safety["eligible_real_source_count"] == 2
    assert safety["real_source_verified"] is True
    assert result["next_safety_note"] == "meta-label enforcement can be considered after separate model-quality approval"


def test_compact_safety_payload_and_text_are_cli_friendly(tmp_path):
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

    report = _report.build_report(_args(tmp_path, log_path=log_path, db_path=db_path))
    payload = _report.build_compact_safety_payload(report)
    text = _report.format_compact_safety_text(payload)

    assert payload["ok"] is True
    assert payload["live_trading_changes"] is False
    assert payload["enforcement_safe"] is False
    assert payload["recommendation"] == "keep_meta_label_enforcement_disabled"
    assert payload["eligible_real_source_count"] == 0
    assert payload["required_eligible_outcomes"] == 2
    assert payload["source_counts"] == {"synthetic_seed": 2}
    assert "prediction_health" not in payload
    assert text.startswith("META_LABEL_SAFETY ok=true enforcement_safe=false ")
    assert "eligible_real_source_count=0/2" in text
    assert "blockers=insufficient_eligible_real_outcomes:0/2,meta_label_source_not_ok_events:1" in text
    assert text.endswith("live_trading_changes=false")
