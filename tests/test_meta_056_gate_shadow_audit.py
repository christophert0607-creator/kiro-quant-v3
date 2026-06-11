import importlib.util
import json
from pathlib import Path

_AUDIT_PATH = Path(__file__).resolve().parents[1] / "self_learn" / "scripts" / "meta_056_gate_shadow_audit.py"
_spec = importlib.util.spec_from_file_location("meta_056_gate_shadow_audit_under_test", _AUDIT_PATH)
assert _spec and _spec.loader
_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_audit)


def _write_jsonl(path: Path, rows: list[dict], *, include_malformed: bool = False) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
        if include_malformed:
            fh.write("not-json\n")


def test_gate_shadow_audit_summarizes_fixture_events(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    _write_jsonl(
        log_path,
        [
            {
                "event": "trade_quality_gate",
                "ts": "2026-06-04T01:00:00Z",
                "shadow": True,
                "symbol": "0700.HK",
                "decision": "ALLOW",
                "score": 0.72,
                "reasons": ["trend_ok", "confidence_ok"],
            },
            {
                "event": "trade_quality_gate",
                "ts": "2026-06-04T01:01:00Z",
                "shadow": True,
                "symbol": "TSLA",
                "decision": "BLOCK",
                "score": "bad-score",
                "reasons": ["weak_quality"],
            },
            {
                "event": "meta_label_gate",
                "timestamp": "2026-06-04T01:02:00+00:00",
                "shadow": True,
                "symbol": "AAPL",
                "decision": "NO_DATA",
                "source_ok": False,
                "reason": "insufficient_real_outcomes",
            },
            {
                "event": "meta_label_gate",
                "created_at": "2026-06-04T01:03:00",
                "shadow": False,
                "symbol": "HK.9988",
                "decision": "CONFIRM",
                "source_ok": True,
                "reason": "paper_broker_history",
            },
            {"event": "unrelated", "symbol": "MSFT"},
        ],
        include_malformed=True,
    )

    result = _audit.audit(log_path, days=0)

    assert result["ok"] is True
    assert result["malformed_score_count"] == 1

    trade_gate = result["gates"]["trade_quality_gate"]
    assert trade_gate["events"] == 2
    assert trade_gate["shadow_counts"] == {"true": 2}
    assert trade_gate["markets"] == {"HK": 1, "US": 1}
    assert trade_gate["decisions"] == {"ALLOW": 1, "BLOCK": 1}
    assert trade_gate["top_reasons"] == {"trend_ok": 1, "confidence_ok": 1, "weak_quality": 1}
    assert trade_gate["top_symbols"] == {"0700.HK": 1, "TSLA": 1}
    assert trade_gate["avg_score"] == 0.72

    meta_gate = result["gates"]["meta_label_gate"]
    assert meta_gate["events"] == 2
    assert meta_gate["shadow_counts"] == {"true": 1, "false": 1}
    assert meta_gate["markets"] == {"US": 1, "HK": 1}
    assert meta_gate["decisions"] == {"NO_DATA": 1, "CONFIRM": 1}
    assert meta_gate["source_ok_counts"] == {"false": 1, "true": 1}
    assert meta_gate["top_reasons"] == {"insufficient_real_outcomes": 1, "paper_broker_history": 1}


def test_gate_shadow_audit_reports_missing_log(tmp_path):
    missing = tmp_path / "missing.jsonl"

    result = _audit.audit(missing, days=1)

    assert result == {
        "ok": False,
        "reason": "log_missing",
        "log": str(missing),
        "days": 1,
        "gates": {},
    }
