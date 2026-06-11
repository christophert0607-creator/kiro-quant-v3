import sqlite3
from datetime import datetime, timezone
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
                pnl_pct REAL,
                source TEXT,
                broker_order_id TEXT,
                recorded_by TEXT,
                provenance_meta TEXT,
                closed_at TEXT
            );
            INSERT INTO predictions VALUES ('p1', 'AAPL', '2026-06-06T00:00:00+00:00');
            INSERT INTO signals VALUES ('s1', 'p1', 'CLOSED', '2026-06-06T00:00:00+00:00');
            INSERT INTO outcomes VALUES ('s1', 1.0, 0.01, 'synthetic_seed', '', 'seed', '{}', '2026-06-06T00:00:00+00:00');
            """
        )


def test_render_progress_block_contains_weekend_training_fields(tmp_path):
    from self_learn.scripts.weekend_training_runner import render_progress_block
    from self_learn.scripts.weekend_training_status import build_status

    _init_self_learn_db(tmp_path / "self_learn" / "trading_bot.db")
    status = build_status(tmp_path)
    block = render_progress_block(status, cycle="collector", steps=[{"name": "status", "status": "ok"}])

    assert "Weekend Training Cycle" in block
    assert "**Mode:** weekend_training_24h" in block
    assert "**Cycle:** collector" in block
    assert "**Retrain Guard:** blocked" in block
    assert "synthetic_only" in block


def test_collector_appends_progress_block(tmp_path):
    from self_learn.scripts.weekend_training_runner import run_cycle

    _init_self_learn_db(tmp_path / "self_learn" / "trading_bot.db")
    result = run_cycle(workspace=tmp_path, mode="collector")

    progress = (tmp_path / "self_learn" / "PROGRESS.md").read_text(encoding="utf-8")
    assert result["status"] == "ok"
    assert "**Cycle:** collector" in progress
    assert "**Mode:** weekend_training_24h" in progress


def test_dry_run_progress_prints_without_writing(tmp_path):
    from self_learn.scripts.weekend_training_runner import run_cycle

    _init_self_learn_db(tmp_path / "self_learn" / "trading_bot.db")
    result = run_cycle(workspace=tmp_path, mode="collector", dry_run_progress=True)

    assert result["status"] == "ok"
    assert "progress_block" in result
    assert not (tmp_path / "self_learn" / "PROGRESS.md").exists()


def test_deep_weekday_without_force_is_skipped(tmp_path):
    from self_learn.scripts.weekend_training_runner import run_cycle

    _init_self_learn_db(tmp_path / "self_learn" / "trading_bot.db")
    monday = datetime(2026, 6, 8, 9, 0, tzinfo=timezone.utc)
    result = run_cycle(workspace=tmp_path, mode="deep", now=monday)

    assert result["status"] == "skipped"
    assert result["reason"] == "outside_weekend_training_window"
    progress = (tmp_path / "self_learn" / "PROGRESS.md").read_text(encoding="utf-8")
    assert "outside_weekend_training_window" in progress


def test_deep_dry_run_does_not_call_heavy_commands_when_guard_blocked(tmp_path):
    from self_learn.scripts.weekend_training_runner import run_cycle

    _init_self_learn_db(tmp_path / "self_learn" / "trading_bot.db")
    called = []

    def fake_run(*args, **kwargs):
        called.append(args)
        return {"name": "fake", "status": "ok", "stdout": "{}", "stderr": "", "returncode": 0}

    saturday = datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc)
    result = run_cycle(workspace=tmp_path, mode="deep", dry_run=True, now=saturday, command_runner=fake_run, live_runtime_checker=lambda: False)

    assert result["status"] == "blocked"
    assert result["reason"] == "insufficient_real_broker_outcomes"
    assert called == []
