#!/usr/bin/env python3
"""Dry-run/promotion-guarded training entry for the trade outcome probability head.

The upgrade plan requires this command to be safe before enough paper/live broker
provenance exists. Therefore --dry-run reports the promotion guard result and
never persists model artifacts.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np

try:
    from self_learn.retrain import validate_meta_model_promotion
except Exception:  # Plain python3 may not have SQLAlchemy; keep dry-run usable.
    def validate_meta_model_promotion(X, y=None, metrics=None, db_path=None, min_eligible_outcomes=100, min_accuracy=0.60):
        db_path = Path(db_path) if db_path is not None else DB_PATH
        X_arr = np.asarray(X)
        y_arr = np.asarray(y if y is not None else [])
        schema_ready = False
        eligible = 0
        if db_path.exists():
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(outcomes)").fetchall()}
                schema_ready = {"source", "broker_order_id", "recorded_by", "provenance_meta"}.issubset(cols)
                if schema_ready:
                    eligible = int(conn.execute("""
                        SELECT COUNT(*) FROM outcomes
                        WHERE source IN ('paper_broker','live_broker')
                          AND ((broker_order_id IS NOT NULL AND broker_order_id != '')
                           OR (provenance_meta IS NOT NULL AND provenance_meta != ''))
                    """).fetchone()[0] or 0)
        accuracy = float((metrics or {}).get("accuracy", 0.0) or 0.0)
        checks = {
            "feature_shape_valid": X_arr.ndim == 2 and X_arr.shape[0] > 0 and X_arr.shape[1] in {6, 9},
            "finite_matrix": bool(X_arr.size) and bool(np.isfinite(X_arr).all()),
            "label_shape_valid": y_arr.ndim == 1 and len(y_arr) == len(X_arr),
            "real_source_verified": schema_ready and eligible >= min_eligible_outcomes,
            "holdout_accuracy_ok": accuracy >= min_accuracy,
            "symbol_coverage_ok": True,
        }
        return {"status": "pass" if all(checks.values()) else "blocked", "reason": None if all(checks.values()) else "meta_model_promotion_guard", "schema_ready": schema_ready, "eligible_real_source_count": eligible, "required_eligible_outcomes": min_eligible_outcomes, "real_source_verified": checks["real_source_verified"], **checks}

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "self_learn" / "trading_bot.db"


def _load_minimal_matrix(db_path: Path, limit: int = 500) -> tuple[np.ndarray, np.ndarray]:
    if not db_path.exists():
        return np.empty((0, 6), dtype=np.float32), np.empty((0,), dtype=np.int64)
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(p.confidence, 0.0), COALESCE(o.pnl, 0.0),
                   COALESCE(o.pnl_pct, 0.0), COALESCE(o.hold_minutes, 0),
                   COALESCE(o.prediction_error, 0.0),
                   CASE WHEN s.action='BUY' THEN 1.0 ELSE 0.0 END
            FROM outcomes o
            JOIN signals s ON s.id = o.signal_id
            LEFT JOIN predictions p ON p.id = s.prediction_id
            ORDER BY COALESCE(o.closed_at, '') DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    if not rows:
        return np.empty((0, 6), dtype=np.float32), np.empty((0,), dtype=np.int64)
    X = np.asarray([[r[0], r[2], r[3] / 1440.0, r[4], r[5], 1.0] for r in rows], dtype=np.float32)
    y = np.asarray([1 if float(r[1]) > 0 else 0 for r in rows], dtype=np.int64)
    return X, y


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Only report guard status; never write artifacts")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--min-eligible", type=int, default=100)
    args = ap.parse_args()

    db_path = Path(args.db)
    X, y = _load_minimal_matrix(db_path)
    metrics = {"accuracy": 0.0 if len(y) == 0 else 0.5, "total_samples": int(len(y))}
    guard = validate_meta_model_promotion(
        X,
        y=y,
        metrics=metrics,
        db_path=db_path,
        min_eligible_outcomes=args.min_eligible,
    )
    result = {
        "status": "blocked" if guard["status"] != "pass" else ("dry_run_pass" if args.dry_run else "ready"),
        "dry_run": bool(args.dry_run),
        "samples": int(len(y)),
        "feature_dim": int(X.shape[1]) if X.ndim == 2 else 0,
        **guard,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
