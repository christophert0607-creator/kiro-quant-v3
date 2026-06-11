#!/usr/bin/env python3
"""Read-only meta-label shadow/provenance safety check.

This hourly check combines recent gate shadow telemetry with immutable outcome
provenance review and returns a conservative enforcement verdict.  It is a
reporting-only guardrail: no live trading modules are imported and nothing is
written to DB, config, logs, or model artifacts.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DECISIONS_LOG = ROOT / "logs" / "decisions.jsonl"
SELF_LEARN_DB = ROOT / "self_learn" / "trading_bot.db"

try:
    from meta_056_gate_shadow_audit import audit as audit_shadow_gates
    from meta_059_provenance_rows_review import review as review_provenance
except ImportError:  # pragma: no cover - defensive path for unusual launch cwd
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from meta_056_gate_shadow_audit import audit as audit_shadow_gates
    from meta_059_provenance_rows_review import review as review_provenance


def _count(mapping: dict[str, Any], key: str) -> int:
    try:
        return int(mapping.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def build_safety_check(
    *,
    log_path: Path = DECISIONS_LOG,
    db_path: Path = SELF_LEARN_DB,
    days: float = 1.0,
    min_eligible_outcomes: int = 100,
) -> dict[str, Any]:
    """Return a conservative read-only verdict for meta-label enforcement safety."""
    shadow = audit_shadow_gates(log_path, days)
    provenance = review_provenance(db_path, min_eligible=min_eligible_outcomes, sample_limit=3)

    blockers: list[str] = []
    warnings: list[str] = []

    if not shadow.get("ok"):
        blockers.append(f"shadow_audit_unavailable:{shadow.get('reason', 'unknown')}")
    if not provenance.get("ok"):
        blockers.append(f"provenance_review_unavailable:{provenance.get('reason', 'unknown')}")
    if not provenance.get("schema_ready", False):
        blockers.append("provenance_schema_not_ready")
    if not provenance.get("real_source_verified", False):
        blockers.append(
            "insufficient_eligible_real_outcomes:"
            f"{provenance.get('eligible_real_source_count', 0)}/"
            f"{provenance.get('required_eligible_outcomes', min_eligible_outcomes)}"
        )

    raw_gates = shadow.get("gates")
    gates: dict[str, Any] = raw_gates if isinstance(raw_gates, dict) else {}
    raw_meta_gate = gates.get("meta_label_gate")
    meta_gate: dict[str, Any] = raw_meta_gate if isinstance(raw_meta_gate, dict) else {}
    if not meta_gate:
        blockers.append("meta_label_gate_shadow_telemetry_missing")
    else:
        false_shadow = _count(meta_gate.get("shadow_counts", {}), "false")
        if false_shadow:
            blockers.append(f"meta_label_gate_non_shadow_events:{false_shadow}")
        false_source_ok = _count(meta_gate.get("source_ok_counts", {}), "false")
        if false_source_ok:
            blockers.append(f"meta_label_source_not_ok_events:{false_source_ok}")
        no_data = _count(meta_gate.get("decisions", {}), "NO_DATA")
        if no_data:
            warnings.append(f"meta_label_no_data_events:{no_data}")

    raw_trade_quality_gate = gates.get("trade_quality_gate")
    trade_quality_gate: dict[str, Any] = raw_trade_quality_gate if isinstance(raw_trade_quality_gate, dict) else {}
    if not trade_quality_gate:
        warnings.append("trade_quality_gate_telemetry_missing")

    enforcement_safe = len(blockers) == 0
    return {
        "ok": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "live_trading_changes": False,
        "days": days,
        "min_eligible_outcomes": int(min_eligible_outcomes),
        "enforcement_safe": enforcement_safe,
        "recommendation": (
            "meta-label enforcement can be considered after separate model-quality approval"
            if enforcement_safe
            else "keep_meta_label_enforcement_disabled"
        ),
        "blockers": blockers,
        "warnings": warnings,
        "shadow_gate_summary": {
            "ok": shadow.get("ok"),
            "reason": shadow.get("reason"),
            "gates": gates,
            "malformed_score_count": shadow.get("malformed_score_count", 0),
        },
        "provenance_summary": {
            "ok": provenance.get("ok"),
            "reason": provenance.get("reason"),
            "schema_ready": provenance.get("schema_ready", False),
            "total_outcomes": provenance.get("total_outcomes", 0),
            "source_counts": provenance.get("source_counts", {}),
            "recorded_by_counts": provenance.get("recorded_by_counts", {}),
            "eligible_real_source_count": provenance.get("eligible_real_source_count", 0),
            "required_eligible_outcomes": provenance.get("required_eligible_outcomes", int(min_eligible_outcomes)),
            "real_source_verified": provenance.get("real_source_verified", False),
            "eligibility_status_counts": provenance.get("eligibility_status_counts", {}),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=1.0, help="lookback window for decisions log; <=0 reads whole log")
    parser.add_argument("--log", type=Path, default=DECISIONS_LOG)
    parser.add_argument("--db", type=Path, default=SELF_LEARN_DB)
    parser.add_argument("--min-eligible-outcomes", type=int, default=100)
    args = parser.parse_args()
    report = build_safety_check(
        log_path=args.log,
        db_path=args.db,
        days=args.days,
        min_eligible_outcomes=args.min_eligible_outcomes,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
