#!/usr/bin/env python3
"""Read-only review of meta-labeling outcome provenance rows.

This small audit answers one safety question before any meta-label enforcement:
are there durable paper/live broker outcome rows with evidence?

It intentionally reads only the self_learn SQLite DB via immutable read-only URI and
never imports live trading modules, writes DB rows, updates config, or touches model
artifacts.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SELF_LEARN_DB = ROOT / "self_learn" / "trading_bot.db"
REQUIRED_COLUMNS = {"source", "broker_order_id", "recorded_by", "provenance_meta"}
ELIGIBLE_SOURCES = {"paper_broker", "live_broker"}
DEFAULT_MIN_ELIGIBLE = 100


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _has_text(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _parse_jsonish(value: Any) -> Any:
    if not _has_text(value):
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return str(value)


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def review(db_path: Path, min_eligible: int = DEFAULT_MIN_ELIGIBLE, sample_limit: int = 5) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "ok": False,
            "reason": "db_missing",
            "db": str(db_path),
            "live_trading_changes": False,
            "schema_ready": False,
            "eligible_real_source_count": 0,
            "real_source_verified": False,
        }

    with _connect_ro(db_path) as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "outcomes" not in tables:
            return {
                "ok": False,
                "reason": "outcomes_table_missing",
                "db": str(db_path),
                "live_trading_changes": False,
                "schema_ready": False,
                "eligible_real_source_count": 0,
                "real_source_verified": False,
            }

        outcome_columns = {row["name"] for row in conn.execute("PRAGMA table_info(outcomes)")}
        schema_ready = REQUIRED_COLUMNS.issubset(outcome_columns)
        total_outcomes = int(conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] or 0)

        if not schema_ready:
            return {
                "ok": True,
                "db": str(db_path),
                "live_trading_changes": False,
                "schema_ready": False,
                "missing_columns": sorted(REQUIRED_COLUMNS - outcome_columns),
                "total_outcomes": total_outcomes,
                "eligible_real_source_count": 0,
                "required_eligible_outcomes": int(min_eligible),
                "real_source_verified": False,
                "recommendation": "Keep meta-label enforcement disabled; provenance schema is not ready.",
            }

        source_counts = {
            str(row["source"] if row["source"] is not None else "NULL"): int(row["count"])
            for row in conn.execute("SELECT source, COUNT(*) AS count FROM outcomes GROUP BY source ORDER BY count DESC")
        }
        recorded_by_counts = {
            str(row["recorded_by"] if row["recorded_by"] is not None else "NULL"): int(row["count"])
            for row in conn.execute(
                "SELECT recorded_by, COUNT(*) AS count FROM outcomes GROUP BY recorded_by ORDER BY count DESC"
            )
        }
        evidence_counts = {
            "with_broker_order_id_any_source": int(
                conn.execute(
                    "SELECT COUNT(*) FROM outcomes WHERE broker_order_id IS NOT NULL AND broker_order_id != ''"
                ).fetchone()[0]
                or 0
            ),
            "with_provenance_meta_any_source": int(
                conn.execute(
                    "SELECT COUNT(*) FROM outcomes WHERE provenance_meta IS NOT NULL AND provenance_meta != ''"
                ).fetchone()[0]
                or 0
            ),
            "eligible_source_with_broker_order_id": int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM outcomes
                    WHERE source IN ('paper_broker', 'live_broker')
                      AND broker_order_id IS NOT NULL AND broker_order_id != ''
                    """
                ).fetchone()[0]
                or 0
            ),
            "eligible_source_with_provenance_meta": int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM outcomes
                    WHERE source IN ('paper_broker', 'live_broker')
                      AND provenance_meta IS NOT NULL AND provenance_meta != ''
                    """
                ).fetchone()[0]
                or 0
            ),
        }
        eligible_count = int(
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

        status_counts = Counter()
        samples: list[dict[str, Any]] = []
        query = """
            SELECT
              o.signal_id,
              p.symbol,
              s.action,
              s.status AS signal_status,
              o.pnl,
              o.pnl_pct,
              o.closed_at,
              o.source,
              o.broker_order_id,
              o.recorded_by,
              o.provenance_meta
            FROM outcomes o
            LEFT JOIN signals s ON s.id = o.signal_id
            LEFT JOIN predictions p ON p.id = s.prediction_id
            ORDER BY o.closed_at DESC, o.signal_id DESC
        """
        for row in conn.execute(query):
            source = row["source"]
            has_evidence_marker = _has_text(row["broker_order_id"]) or _has_text(row["provenance_meta"])
            is_eligible_source = source in ELIGIBLE_SOURCES
            has_broker_evidence = is_eligible_source and has_evidence_marker
            if has_broker_evidence:
                status = "eligible_real"
            elif is_eligible_source and not has_evidence_marker:
                status = "real_source_missing_evidence"
            elif source in (None, ""):
                status = "missing_source"
            else:
                status = "non_real_or_synthetic"
            status_counts[status] += 1
            if len(samples) < max(0, int(sample_limit)):
                item = _row_dict(row)
                item["eligibility_status"] = status
                item["has_broker_evidence"] = has_broker_evidence
                item["has_any_evidence_marker"] = has_evidence_marker
                item["provenance_meta_preview"] = _parse_jsonish(item.pop("provenance_meta"))
                samples.append(item)

    real_source_verified = schema_ready and eligible_count >= int(min_eligible)
    return {
        "ok": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "db": str(db_path),
        "live_trading_changes": False,
        "schema_ready": schema_ready,
        "required_columns": sorted(REQUIRED_COLUMNS),
        "total_outcomes": total_outcomes,
        "source_counts": source_counts,
        "recorded_by_counts": recorded_by_counts,
        "evidence_counts": evidence_counts,
        "eligibility_status_counts": dict(status_counts),
        "eligible_sources": sorted(ELIGIBLE_SOURCES),
        "eligible_real_source_count": eligible_count,
        "required_eligible_outcomes": int(min_eligible),
        "real_source_verified": real_source_verified,
        "sample_recent_outcomes": samples,
        "recommendation": (
            "Promotion/enforcement may be considered only after separate model-quality checks."
            if real_source_verified
            else "Keep meta-label enforcement disabled; no sufficient durable paper/live broker provenance rows."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=SELF_LEARN_DB)
    parser.add_argument("--min-eligible-outcomes", type=int, default=DEFAULT_MIN_ELIGIBLE)
    parser.add_argument("--sample-limit", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(review(args.db, args.min_eligible_outcomes, args.sample_limit), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
