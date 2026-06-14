#!/usr/bin/env python3
"""meta_024 — Phase 4 approval checklist for meta-labeling promotion.

Read-only/report-only helper. It produces an operator-facing checklist of the
explicit approval gates, shadow-mode guardrails, rollback triggers, and blocked
live-integration actions that must be reviewed before any meta-labeling logic is
wired into live trading.

This script deliberately does not import live trading modules and does not write
DB/config/model/runtime state.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]
DEFAULT_STATUS_PATH = WORKSPACE / "dev" / "meta_labeling" / "STATUS.json"


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    category: str
    requirement: str
    evidence: str
    required_before_live: bool = True


def build_checklist() -> dict:
    approval_gates = [
        ChecklistItem(
            id="gate_user_approval",
            category="approval",
            requirement="Named user approval is recorded before modifying LiveTradingLoop, order routing, sizing, stop-loss, or risk enforcement.",
            evidence="Approval note or ticket linked in DEVLOG/STATUS before any Phase 4 code change.",
        ),
        ChecklistItem(
            id="gate_metric_bundle",
            category="promotion_metric",
            requirement="Promotion decision uses coverage, covered accuracy, abs-PnL weighted accuracy, and P&L delta together; raw accuracy alone is not sufficient.",
            evidence="Latest read-only metric study report attached with synthetic/live-data provenance clearly labelled.",
        ),
        ChecklistItem(
            id="gate_shadow_first",
            category="shadow_mode",
            requirement="Initial integration runs in shadow mode only: log would-confirm/would-reject/would-reverse decisions without changing submitted orders.",
            evidence="Shadow report demonstrates no order/risk mutation and includes decision distribution by symbol/action.",
        ),
        ChecklistItem(
            id="gate_replay_smoke",
            category="verification",
            requirement="Replay/smoke tests cover CONFIRM, REJECT, REVERSE, NO_DATA, missing payloads, malformed payloads, and rollback-off behavior.",
            evidence="Focused pytest output and py_compile commands recorded in DEVLOG.",
        ),
        ChecklistItem(
            id="gate_operator_observability",
            category="monitoring",
            requirement="Operator-visible summary reports live_trading_changes=false/true explicitly, enforcement state, blockers, and safe fallback status.",
            evidence="Cron/chat-friendly one-line safety summary sample checked into dev/meta_labeling or self_learn/scripts.",
        ),
    ]

    shadow_guardrails = [
        ChecklistItem(
            id="guardrail_no_order_mutation",
            category="shadow_guardrail",
            requirement="Shadow-mode decisions must not mutate order side, quantity, price, stop-loss, or broker submission path.",
            evidence="Tests assert order intent before/after shadow evaluator is unchanged.",
        ),
        ChecklistItem(
            id="guardrail_fail_open_base_strategy",
            category="shadow_guardrail",
            requirement="Missing/invalid meta-labeling data falls back to base strategy behavior and records a blocker instead of enforcing a meta decision.",
            evidence="Malformed and missing safety payload tests pass.",
        ),
        ChecklistItem(
            id="guardrail_config_kill_switch",
            category="shadow_guardrail",
            requirement="A config kill switch can disable all meta-labeling enforcement without redeploying live trading code.",
            evidence="Rollback-off smoke test and operator runbook entry exist.",
        ),
    ]

    rollback_triggers = [
        ChecklistItem(
            id="rollback_any_live_order_delta_without_approval",
            category="rollback",
            requirement="Any live order delta caused by meta-labeling before explicit approval triggers immediate disable and investigation.",
            evidence="Alert/runbook names owner, command, and verification query.",
        ),
        ChecklistItem(
            id="rollback_metric_regression",
            category="rollback",
            requirement="Shadow metrics regress below approved gate thresholds for coverage, weighted accuracy, or P&L delta over the agreed window.",
            evidence="Daily health report shows threshold breach and blocker state.",
        ),
        ChecklistItem(
            id="rollback_payload_integrity",
            category="rollback",
            requirement="Malformed/missing safety payloads, non-boolean live_trading_changes flags, or stale telemetry trigger safe fallback.",
            evidence="Cron safety summary emits critical alert and enforcement_safe=false.",
        ),
    ]

    blocked_actions = [
        "Do not edit v3_pipeline/core/main_loop.py for meta-labeling enforcement without user approval.",
        "Do not change live order routing, broker calls, position sizing, stop-loss, risk checks, or runtime enforcement logic.",
        "Do not promote synthetic-only results as live-ready without clear provenance and user approval.",
    ]

    return {
        "task": "meta_024",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Phase 4 approval checklist before any live meta-labeling integration.",
        "approval_required_before_live_integration": True,
        "live_trading_changes": False,
        "approval_gates": [asdict(item) for item in approval_gates],
        "shadow_mode_guardrails": [asdict(item) for item in shadow_guardrails],
        "rollback_triggers": [asdict(item) for item in rollback_triggers],
        "blocked_without_user_approval": blocked_actions,
        "recommended_next_task": "meta_025_shadow_mode_summary_fixture",
    }


def write_markdown(report: dict, output_path: Path) -> None:
    lines = [
        "# Meta-Labeling Phase 4 Approval Checklist",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        "This is a read-only/report-only artifact. It does **not** approve live integration.",
        "",
        f"- Approval required before live integration: `{report['approval_required_before_live_integration']}`",
        f"- Live trading/risk logic changed: `{report['live_trading_changes']}`",
        "",
        "## Approval gates",
    ]
    for item in report["approval_gates"]:
        lines.extend([
            f"### {item['id']}",
            f"- Category: `{item['category']}`",
            f"- Requirement: {item['requirement']}",
            f"- Evidence: {item['evidence']}",
            "",
        ])

    lines.append("## Shadow-mode guardrails")
    for item in report["shadow_mode_guardrails"]:
        lines.extend([
            f"### {item['id']}",
            f"- Requirement: {item['requirement']}",
            f"- Evidence: {item['evidence']}",
            "",
        ])

    lines.append("## Rollback triggers")
    for item in report["rollback_triggers"]:
        lines.extend([
            f"### {item['id']}",
            f"- Requirement: {item['requirement']}",
            f"- Evidence: {item['evidence']}",
            "",
        ])

    lines.append("## Blocked without explicit user approval")
    lines.extend(f"- {action}" for action in report["blocked_without_user_approval"])
    lines.extend(["", f"Recommended next task: `{report['recommended_next_task']}`", ""])
    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--write-md", type=Path, help="Optional markdown output path for operator review docs")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_checklist()
    if args.write_md:
        write_markdown(report, args.write_md)
    if args.format == "markdown":
        tmp_path = args.write_md or Path("/tmp/meta_024_phase4_approval_checklist.md")
        if not args.write_md:
            write_markdown(report, tmp_path)
        print(tmp_path.read_text(encoding="utf-8"))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
