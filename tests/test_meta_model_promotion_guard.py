import json
import sqlite3
from pathlib import Path

import numpy as np

from self_learn import retrain


def _init_db(path: Path, source: str = "synthetic_seed", rows: int = 100, with_evidence: bool = False) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE outcomes (
            signal_id TEXT PRIMARY KEY,
            exit_price REAL,
            pnl REAL,
            pnl_pct REAL,
            hold_minutes INTEGER,
            prediction_error REAL,
            source TEXT,
            broker_order_id TEXT,
            recorded_by TEXT,
            provenance_meta TEXT,
            closed_at TEXT
        );
        """
    )
    for i in range(rows):
        broker_id = f"ord-{i}" if with_evidence else None
        meta = json.dumps({"market": "US", "trd_env": "SIMULATE"}) if with_evidence else None
        conn.execute(
            "INSERT INTO outcomes VALUES (?, 100, ?, ?, 30, 1.0, ?, ?, 'pytest', ?, datetime('now'))",
            (f"sig-{i}", 1.0 if i % 2 else -1.0, 0.01 if i % 2 else -0.01, source, broker_id, meta),
        )
    conn.commit()
    conn.close()


def test_promotion_guard_blocks_synthetic_only_outcomes(tmp_path):
    db = tmp_path / "trading_bot.db"
    _init_db(db, source="synthetic_seed", rows=100, with_evidence=False)

    result = retrain.validate_meta_model_promotion(
        X=np.ones((100, 6), dtype=np.float32),
        y=np.array([0, 1] * 50),
        metrics={"accuracy": 0.75},
        db_path=db,
        min_eligible_outcomes=100,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "meta_model_promotion_guard"
    assert result["schema_ready"] is True
    assert result["eligible_real_source_count"] == 0
    assert result["real_source_verified"] is False


def test_promotion_guard_allows_real_paper_broker_evidence(tmp_path):
    db = tmp_path / "trading_bot.db"
    _init_db(db, source="paper_broker", rows=100, with_evidence=True)

    result = retrain.validate_meta_model_promotion(
        X=np.ones((100, 6), dtype=np.float32),
        y=np.array([0, 1] * 50),
        metrics={"accuracy": 0.75},
        db_path=db,
        min_eligible_outcomes=100,
    )

    assert result["status"] == "pass"
    assert result["eligible_real_source_count"] == 100
    assert result["real_source_verified"] is True
