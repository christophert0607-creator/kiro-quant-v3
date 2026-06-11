import json
import sqlite3
from pathlib import Path


def _init_self_learn_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE predictions (id TEXT PRIMARY KEY, symbol TEXT, created_at TEXT);
            CREATE TABLE signals (id TEXT PRIMARY KEY, prediction_id TEXT, status TEXT, created_at TEXT);
            CREATE TABLE outcomes (
                signal_id TEXT PRIMARY KEY,
                pnl REAL,
                source TEXT,
                broker_order_id TEXT,
                recorded_by TEXT,
                provenance_meta TEXT,
                closed_at TEXT
            );
            """
        )
        conn.execute("INSERT INTO predictions VALUES ('p1', 'AAPL', '2026-06-06T00:00:00+00:00')")
        conn.execute("INSERT INTO signals VALUES ('s1', 'p1', 'CLOSED', '2026-06-06T00:01:00+00:00')")
        conn.execute(
            "INSERT INTO outcomes VALUES ('s1', 12.3, 'paper_broker', 'B123', 'feedback', '{}', '2026-06-06T00:10:00+00:00')"
        )
        conn.execute("INSERT INTO predictions VALUES ('p2', 'TSLA', '2026-06-06T00:00:00+00:00')")
        conn.execute("INSERT INTO signals VALUES ('s2', 'p2', 'CLOSED', '2026-06-06T00:01:00+00:00')")
        conn.execute(
            "INSERT INTO outcomes VALUES ('s2', -5.0, 'synthetic_seed', '', 'seed', '{}', '2026-06-06T00:10:00+00:00')"
        )


def test_weekend_training_status_reports_required_keys(tmp_path):
    from self_learn.scripts.weekend_training_status import build_status

    _init_self_learn_db(tmp_path / "self_learn" / "trading_bot.db")
    status = build_status(workspace=tmp_path)

    for key in ["mode", "stats", "eligible_real_source_count", "guard", "latest_metrics"]:
        assert key in status
    assert status["mode"] == "weekend_training_24h"
    assert status["stats"]["predictions"] == 2
    assert status["stats"]["signals"] == 2
    assert status["stats"]["outcomes"] == 2
    assert status["eligible_real_source_count"] == 1
    assert status["guard"]["status"] == "blocked"
    assert status["guard"]["reason"] == "insufficient_real_broker_outcomes"


def test_weekend_training_status_reads_latest_training_metrics(tmp_path):
    from self_learn.scripts.weekend_training_status import build_status

    _init_self_learn_db(tmp_path / "self_learn" / "trading_bot.db")
    log = tmp_path / "self_learn" / "models" / "training_log.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps({"trained_at": "old", "metrics": {"accuracy": 0.1}}) + "\n"
        + json.dumps({"trained_at": "new", "metrics": {"accuracy": 0.79, "total_samples": 149}}) + "\n",
        encoding="utf-8",
    )

    status = build_status(workspace=tmp_path)

    assert status["latest_metrics"]["trained_at"] == "new"
    assert status["latest_metrics"]["metrics"]["accuracy"] == 0.79


def test_weekend_training_status_cli_outputs_json(tmp_path, capsys):
    from self_learn.scripts.weekend_training_status import main

    _init_self_learn_db(tmp_path / "self_learn" / "trading_bot.db")
    rc = main(["--workspace", str(tmp_path)])

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "weekend_training_24h"
    assert out["stats"]["closed"] == 2
