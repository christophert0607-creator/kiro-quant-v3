#!/usr/bin/env python3
"""Read-only weekend 24h training status for Kiro Quant V3.

This module deliberately avoids importing the live trading runtime.  It reads
SQLite DBs and JSONL logs only, so it is safe for cron/progress reporting.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODE = "weekend_training_24h"
REQUIRED_PROVENANCE_COLUMNS = {"source", "broker_order_id", "recorded_by", "provenance_meta"}
ELIGIBLE_OUTCOME_SOURCES = {"paper_broker", "live_broker"}


def _count_table(conn: sqlite3.Connection, table: str, where: str | None = None) -> int:
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql).fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _source_counts(conn: sqlite3.Connection, columns: set[str]) -> dict[str, int]:
    if "source" not in columns:
        return {}
    try:
        return {
            str(row[0] if row[0] is not None else "NULL"): int(row[1] or 0)
            for row in conn.execute("SELECT source, COUNT(*) FROM outcomes GROUP BY source")
        }
    except sqlite3.Error:
        return {}


def _eligible_real_source_count(conn: sqlite3.Connection, columns: set[str]) -> int:
    if not REQUIRED_PROVENANCE_COLUMNS.issubset(columns):
        return 0
    try:
        return int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM outcomes
                WHERE source IN ('paper_broker', 'live_broker')
                  AND (
                    (broker_order_id IS NOT NULL AND broker_order_id != '')
                    OR (provenance_meta IS NOT NULL AND provenance_meta != '')
                  )
                """
            ).fetchone()[0]
            or 0
        )
    except sqlite3.Error:
        return 0


def _read_self_learn_stats(db_path: Path, min_eligible_outcomes: int) -> dict[str, Any]:
    empty = {
        "db": str(db_path),
        "db_exists": db_path.exists(),
        "schema_ready": False,
        "stats": {"predictions": 0, "signals": 0, "closed": 0, "outcomes": 0, "total_pnl": 0.0, "avg_pnl_pct": None},
        "eligible_real_source_count": 0,
        "source_counts": {},
        "guard": {
            "status": "blocked",
            "reason": "db_missing" if not db_path.exists() else "schema_not_ready",
            "required_eligible_outcomes": min_eligible_outcomes,
            "real_source_verified": False,
        },
    }
    if not db_path.exists():
        return empty

    uri = f"file:{db_path}?mode=ro&immutable=1"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            outcome_cols = _table_columns(conn, "outcomes")
            schema_ready = REQUIRED_PROVENANCE_COLUMNS.issubset(outcome_cols)
            predictions = _count_table(conn, "predictions")
            signals = _count_table(conn, "signals")
            closed = _count_table(conn, "signals", "status = 'CLOSED'")
            outcomes = _count_table(conn, "outcomes")
            try:
                pnl_row = conn.execute("SELECT COALESCE(SUM(pnl), 0.0), AVG(pnl_pct) FROM outcomes").fetchone()
                total_pnl = round(float(pnl_row[0] or 0.0), 6)
                avg_pnl_pct = None if pnl_row[1] is None else round(float(pnl_row[1]), 6)
            except sqlite3.Error:
                total_pnl = 0.0
                avg_pnl_pct = None
            eligible = _eligible_real_source_count(conn, outcome_cols)
            sources = _source_counts(conn, outcome_cols)
    except sqlite3.Error as exc:
        empty["guard"]["reason"] = f"db_read_error:{exc}"
        return empty

    guard_status = "pass" if schema_ready and eligible >= min_eligible_outcomes else "blocked"
    if not schema_ready:
        reason = "schema_not_ready"
    elif eligible < min_eligible_outcomes:
        reason = "insufficient_real_broker_outcomes"
    else:
        reason = None
    return {
        "db": str(db_path),
        "db_exists": True,
        "schema_ready": schema_ready,
        "stats": {
            "predictions": predictions,
            "signals": signals,
            "closed": closed,
            "outcomes": outcomes,
            "total_pnl": total_pnl,
            "avg_pnl_pct": avg_pnl_pct,
        },
        "eligible_real_source_count": eligible,
        "source_counts": sources,
        "guard": {
            "status": guard_status,
            "reason": reason,
            "required_eligible_outcomes": min_eligible_outcomes,
            "real_source_verified": schema_ready and eligible >= min_eligible_outcomes,
            "schema_ready": schema_ready,
        },
    }


def _read_market_data_stats(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"db": str(db_path), "db_exists": False, "market_data_rows": 0, "symbols": 0}
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True) as conn:
            return {
                "db": str(db_path),
                "db_exists": True,
                "market_data_rows": _count_table(conn, "market_data"),
                "symbols": int(conn.execute("SELECT COUNT(DISTINCT symbol) FROM market_data").fetchone()[0] or 0),
            }
    except sqlite3.Error as exc:
        return {"db": str(db_path), "db_exists": True, "market_data_rows": 0, "symbols": 0, "error": str(exc)}


def latest_training_log_entry(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    last: dict[str, Any] | None = None
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                last = item
    if last is None:
        return None
    return {
        "trained_at": last.get("trained_at") or last.get("created_at"),
        "version_id": last.get("version_id"),
        "model_path": last.get("model_path"),
        "metrics": last.get("metrics") if isinstance(last.get("metrics"), dict) else {},
        "promotion_guard": last.get("promotion_guard") if isinstance(last.get("promotion_guard"), dict) else {},
    }


def build_status(workspace: str | Path = ".", min_eligible_outcomes: int = 100) -> dict[str, Any]:
    root = Path(workspace).resolve()
    self_learn = _read_self_learn_stats(root / "self_learn" / "trading_bot.db", min_eligible_outcomes)
    market_data = _read_market_data_stats(root / "kiro_quant.db")
    latest_metrics = latest_training_log_entry(root / "self_learn" / "models" / "training_log.jsonl")
    return {
        "mode": MODE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(root),
        "stats": self_learn["stats"],
        "market_data": market_data,
        "schema_ready": self_learn["schema_ready"],
        "eligible_real_source_count": self_learn["eligible_real_source_count"],
        "source_counts": self_learn["source_counts"],
        "guard": self_learn["guard"],
        "latest_metrics": latest_metrics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only weekend 24h training status")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--min-eligible-outcomes", type=int, default=100)
    args = parser.parse_args(argv)
    print(json.dumps(build_status(args.workspace, args.min_eligible_outcomes), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
