#!/usr/bin/env python3
"""Compact read-only daily meta-labeling health report.

Combines three safe telemetry sources:
- prediction / gate health from ``logs/decisions.jsonl``
- shadow gate audit summary from ``meta_056_gate_shadow_audit.py``
- outcome provenance eligibility from ``self_learn/trading_bot.db``

This script is intentionally read-only: it does not import live trading modules and
never writes DB, config, or model artifacts.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DECISIONS_LOG = ROOT / "logs" / "decisions.jsonl"
SELF_LEARN_DB = ROOT / "self_learn" / "trading_bot.db"
TRAINING_LOG = ROOT / "self_learn" / "models" / "training_log.jsonl"
REQUIRED_PROVENANCE_COLUMNS = {"source", "broker_order_id", "recorded_by", "provenance_meta"}
ELIGIBLE_OUTCOME_SOURCES = {"paper_broker", "live_broker"}

try:
    from meta_056_gate_shadow_audit import audit as audit_shadow_gates
    from meta_061_shadow_provenance_safety_check import build_safety_check
except ImportError:  # pragma: no cover - defensive path for unusual launch cwd
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from meta_056_gate_shadow_audit import audit as audit_shadow_gates
    from meta_061_shadow_provenance_safety_check import build_safety_check


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _market(symbol: Any) -> str:
    text = str(symbol or "").upper()
    if text.endswith(".HK") or text.startswith("HK."):
        return "HK"
    if text:
        return "US"
    return "UNKNOWN"


def collect_prediction_health(log_path: Path, days: float) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None
    stats: dict[str, Any] = defaultdict(
        lambda: {
            "predictions": 0,
            "quality": Counter(),
            "meta": Counter(),
            "orders_attempted": 0,
            "orders_result": Counter(),
            "outcome_probs": [],
        }
    )

    if not log_path.exists():
        return {"ok": False, "reason": "log_missing", "log": str(log_path), "markets": {}}

    with log_path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(ev.get("ts") or ev.get("timestamp") or ev.get("created_at"))
            if cutoff is not None and ts is not None and ts < cutoff:
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else ev
            symbol = data.get("symbol") or ev.get("symbol") or ""
            market = _market(symbol)
            event = str(ev.get("event") or ev.get("type") or "")
            bucket = stats[market]
            if event in {"model_predict", "prediction"}:
                bucket["predictions"] += 1
            elif event in {"trade_quality_gate", "trade_quality"}:
                bucket["quality"][str(data.get("decision", "UNKNOWN"))] += 1
            elif event in {"meta_label_gate", "meta_label"}:
                bucket["meta"][str(data.get("decision", "UNKNOWN"))] += 1
            elif event == "order_attempt":
                bucket["orders_attempted"] += 1
            elif event == "order_result":
                bucket["orders_result"][str(data.get("status", "UNKNOWN"))] += 1
            elif event == "outcome_head":
                try:
                    bucket["outcome_probs"].append(float(data.get("prob_profit")))
                except (TypeError, ValueError):
                    pass

    markets = {}
    for market, bucket in sorted(stats.items()):
        probs = bucket["outcome_probs"]
        markets[market] = {
            "predictions": bucket["predictions"],
            "quality_decisions": dict(bucket["quality"]),
            "meta_decisions": dict(bucket["meta"]),
            "outcome_head_avg_probability": round(sum(probs) / len(probs), 4) if probs else None,
            "orders_attempted": bucket["orders_attempted"],
            "order_results": dict(bucket["orders_result"]),
        }
    return {"ok": True, "log": str(log_path), "days": days, "markets": markets}


def collect_provenance_eligibility(db_path: Path, min_eligible_outcomes: int = 100) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "ok": False,
            "reason": "db_missing",
            "db": str(db_path),
            "schema_ready": False,
            "eligible_real_source_count": 0,
            "real_source_verified": False,
            "required_eligible_outcomes": min_eligible_outcomes,
        }

    uri = f"file:{db_path}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "outcomes" not in tables:
            return {
                "ok": False,
                "reason": "outcomes_table_missing",
                "db": str(db_path),
                "schema_ready": False,
                "eligible_real_source_count": 0,
                "real_source_verified": False,
                "required_eligible_outcomes": min_eligible_outcomes,
            }

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(outcomes)")}
        schema_ready = REQUIRED_PROVENANCE_COLUMNS.issubset(columns)
        total_outcomes = int(conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] or 0)
        source_counts: dict[str, int] = {}
        recorded_by_counts: dict[str, int] = {}
        eligible_count = 0
        if schema_ready:
            source_counts = {
                str(row["source"] if row["source"] is not None else "NULL"): int(row["count"])
                for row in conn.execute("SELECT source, COUNT(*) AS count FROM outcomes GROUP BY source")
            }
            recorded_by_counts = {
                str(row["recorded_by"] if row["recorded_by"] is not None else "NULL"): int(row["count"])
                for row in conn.execute("SELECT recorded_by, COUNT(*) AS count FROM outcomes GROUP BY recorded_by")
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

    return {
        "ok": True,
        "db": str(db_path),
        "schema_ready": schema_ready,
        "required_columns": sorted(REQUIRED_PROVENANCE_COLUMNS),
        "missing_columns": sorted(REQUIRED_PROVENANCE_COLUMNS - columns),
        "total_outcomes": total_outcomes,
        "eligible_sources": sorted(ELIGIBLE_OUTCOME_SOURCES),
        "eligible_real_source_count": eligible_count,
        "required_eligible_outcomes": int(min_eligible_outcomes),
        "real_source_verified": schema_ready and eligible_count >= int(min_eligible_outcomes),
        "source_counts": source_counts,
        "recorded_by_counts": recorded_by_counts,
    }


def latest_training_log_entry(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    last: dict[str, Any] | None = None
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    if last is None:
        return None
    metrics = last.get("metrics") if isinstance(last.get("metrics"), dict) else {}
    guard = last.get("promotion_guard") if isinstance(last.get("promotion_guard"), dict) else {}
    return {
        "trained_at": last.get("trained_at") or last.get("created_at"),
        "version_id": last.get("version_id"),
        "metrics": metrics,
        "promotion_guard": guard,
    }


def _compact_safety_summary(safety_check: dict[str, Any]) -> dict[str, Any]:
    """Return the small safety verdict block shown in the daily health report."""
    raw_provenance = safety_check.get("provenance_summary")
    provenance: dict[str, Any] = raw_provenance if isinstance(raw_provenance, dict) else {}
    raw_shadow = safety_check.get("shadow_gate_summary")
    shadow: dict[str, Any] = raw_shadow if isinstance(raw_shadow, dict) else {}
    raw_gates = shadow.get("gates")
    gates: dict[str, Any] = raw_gates if isinstance(raw_gates, dict) else {}
    raw_meta_gate = gates.get("meta_label_gate")
    meta_gate: dict[str, Any] = raw_meta_gate if isinstance(raw_meta_gate, dict) else {}
    raw_trade_quality_gate = gates.get("trade_quality_gate")
    trade_quality_gate: dict[str, Any] = raw_trade_quality_gate if isinstance(raw_trade_quality_gate, dict) else {}
    return {
        "ok": safety_check.get("ok", False),
        "live_trading_changes": False,
        "enforcement_safe": bool(safety_check.get("enforcement_safe", False)),
        "recommendation": safety_check.get("recommendation"),
        "blockers": list(safety_check.get("blockers") or []),
        "warnings": list(safety_check.get("warnings") or []),
        "meta_label_gate_events": int(meta_gate.get("events", meta_gate.get("total", 0)) or 0),
        "trade_quality_gate_events": int(trade_quality_gate.get("events", trade_quality_gate.get("total", 0)) or 0),
        "eligible_real_source_count": int(provenance.get("eligible_real_source_count", 0) or 0),
        "required_eligible_outcomes": int(provenance.get("required_eligible_outcomes", 0) or 0),
        "real_source_verified": bool(provenance.get("real_source_verified", False)),
        "source_counts": provenance.get("source_counts", {}),
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    prediction = collect_prediction_health(args.log, args.days)
    shadow = audit_shadow_gates(args.log, args.days)
    provenance = collect_provenance_eligibility(args.db, args.min_eligible_outcomes)
    safety_check = build_safety_check(
        log_path=args.log,
        db_path=args.db,
        days=args.days,
        min_eligible_outcomes=args.min_eligible_outcomes,
    )
    latest_training = latest_training_log_entry(args.training_log)
    safety_summary = _compact_safety_summary(safety_check)
    return {
        "ok": bool(prediction.get("ok"))
        and bool(shadow.get("ok"))
        and bool(provenance.get("ok"))
        and bool(safety_check.get("ok")),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "live_trading_changes": False,
        "prediction_health": prediction,
        "shadow_gate_audit": shadow,
        "provenance_eligibility": provenance,
        "meta_label_safety_summary": safety_summary,
        "latest_training_log": latest_training,
        "next_safety_note": safety_summary.get("recommendation")
        or "Keep meta-label enforcement disabled until real_source_verified=true with durable paper/live broker provenance.",
    }


def build_compact_safety_payload(report: dict[str, Any]) -> dict[str, Any]:
    """Return a CLI-friendly JSON payload focused on the promotion safety verdict."""
    raw_safety = report.get("meta_label_safety_summary")
    safety: dict[str, Any] = raw_safety if isinstance(raw_safety, dict) else {}
    return {
        "ok": bool(report.get("ok")) and bool(safety.get("ok")),
        "generated_at_utc": report.get("generated_at_utc"),
        "days": report.get("days"),
        "live_trading_changes": False,
        "enforcement_safe": bool(safety.get("enforcement_safe", False)),
        "recommendation": safety.get("recommendation") or report.get("next_safety_note"),
        "blockers": list(safety.get("blockers") or []),
        "warnings": list(safety.get("warnings") or []),
        "meta_label_gate_events": int(safety.get("meta_label_gate_events", 0) or 0),
        "trade_quality_gate_events": int(safety.get("trade_quality_gate_events", 0) or 0),
        "eligible_real_source_count": int(safety.get("eligible_real_source_count", 0) or 0),
        "required_eligible_outcomes": int(safety.get("required_eligible_outcomes", 0) or 0),
        "real_source_verified": bool(safety.get("real_source_verified", False)),
        "source_counts": safety.get("source_counts", {}),
    }


def format_compact_safety_text(payload: dict[str, Any]) -> str:
    """Format the compact safety payload as one grep-friendly status line."""
    eligible = f"{payload.get('eligible_real_source_count', 0)}/{payload.get('required_eligible_outcomes', 0)}"
    blockers = ",".join(str(item) for item in payload.get("blockers") or []) or "none"
    warnings = ",".join(str(item) for item in payload.get("warnings") or []) or "none"
    recommendation = payload.get("recommendation") or "none"
    return (
        "META_LABEL_SAFETY "
        f"ok={str(bool(payload.get('ok'))).lower()} "
        f"enforcement_safe={str(bool(payload.get('enforcement_safe'))).lower()} "
        f"recommendation={recommendation} "
        f"eligible_real_source_count={eligible} "
        f"real_source_verified={str(bool(payload.get('real_source_verified'))).lower()} "
        f"meta_label_gate_events={int(payload.get('meta_label_gate_events', 0) or 0)} "
        f"trade_quality_gate_events={int(payload.get('trade_quality_gate_events', 0) or 0)} "
        f"blockers={blockers} "
        f"warnings={warnings} "
        "live_trading_changes=false"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=1.0, help="lookback window; <=0 reads whole decisions log")
    parser.add_argument("--log", type=Path, default=DECISIONS_LOG)
    parser.add_argument("--db", type=Path, default=SELF_LEARN_DB)
    parser.add_argument("--training-log", type=Path, default=TRAINING_LOG)
    parser.add_argument("--min-eligible-outcomes", type=int, default=100)
    parser.add_argument(
        "--safety-summary",
        choices=("json", "text"),
        help="print only the compact meta-label safety verdict in JSON or one-line text form",
    )
    args = parser.parse_args()
    report = build_report(args)
    if args.safety_summary:
        payload = build_compact_safety_payload(report)
        if args.safety_summary == "text":
            print(format_compact_safety_text(payload))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
