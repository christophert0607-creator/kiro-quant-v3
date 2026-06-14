#!/usr/bin/env python3
"""meta_025 — Shadow-mode operator safety summary fixture.

Read-only/report-only helper for the Phase 4 pre-integration gate. It emits a
cron/operator-facing safety summary that demonstrates the expected shadow-mode
state before any live-loop integration work is approved:

- enforcement_safe=false (not approved for enforcement)
- live_trading_changes=false (this fixture does not touch live trading/risk code)
- approval_required=true
- shadow_mode_only=true

The script deliberately does not import live trading modules and does not write
DB/config/model/runtime state.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]


def build_summary() -> dict:
    blockers = [
        "explicit_user_approval_missing",
        "shadow_mode_not_integrated",
        "live_replay_evidence_missing",
    ]
    return {
        "task": "meta_025",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Operator-facing shadow-mode safety summary fixture before live integration.",
        "mode": "shadow_fixture",
        "shadow_mode_only": True,
        "approval_required": True,
        "enforcement_safe": False,
        "live_trading_changes": False,
        "order_mutation_allowed": False,
        "risk_logic_mutation_allowed": False,
        "safe_fallback": "base_strategy_unchanged",
        "blockers": blockers,
        "operator_summary": {
            "status": "BLOCKED_FOR_LIVE_ENFORCEMENT",
            "line": render_summary_line(
                enforcement_safe=False,
                live_trading_changes=False,
                approval_required=True,
                shadow_mode_only=True,
                blocker_count=len(blockers),
            ),
        },
        "blocked_without_user_approval": [
            "Do not wire meta-labeling decisions into LiveTradingLoop enforcement.",
            "Do not change order side, quantity, routing, position sizing, stops, or risk checks.",
            "Do not enable live REJECT/REVERSE behavior from this fixture.",
        ],
        "recommended_next_task": "meta_026_shadow_payload_validation_fixture",
    }


def render_summary_line(
    *,
    enforcement_safe: bool,
    live_trading_changes: bool,
    approval_required: bool,
    shadow_mode_only: bool,
    blocker_count: int,
) -> str:
    """Return a compact cron/chat-friendly safety line."""
    return (
        "meta_labeling_shadow_summary "
        f"enforcement_safe={str(enforcement_safe).lower()} "
        f"live_trading_changes={str(live_trading_changes).lower()} "
        f"approval_required={str(approval_required).lower()} "
        f"shadow_mode_only={str(shadow_mode_only).lower()} "
        f"blockers={blocker_count}"
    )


def render_text(summary: dict) -> str:
    return "\n".join(
        [
            summary["operator_summary"]["line"],
            f"status={summary['operator_summary']['status']}",
            f"safe_fallback={summary['safe_fallback']}",
            "blockers=" + ",".join(summary["blockers"]),
            "live_trading_changes=false",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_summary()
    if args.format == "text":
        print(render_text(summary))
    else:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
