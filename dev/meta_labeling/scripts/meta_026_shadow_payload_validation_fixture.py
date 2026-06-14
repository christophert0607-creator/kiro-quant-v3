#!/usr/bin/env python3
"""meta_026 — Shadow payload validation fixture.

Read-only/report-only helper for validating operator shadow payload safety flags.
It deliberately does not import live trading modules and does not write DB/config/
model/runtime state.

The fixture focuses on the Phase 4 guardrail that missing, malformed, true, or
non-boolean ``live_trading_changes`` payloads must never be treated as safe. Any
value other than explicit ``False`` is classified as unexpected and keeps
``enforcement_safe=false``.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

FIXTURES: dict[str, Any] = {
    "valid_false": {
        "task": "meta_026_fixture_valid_false",
        "live_trading_changes": False,
        "approval_required": True,
        "shadow_mode_only": True,
    },
    "missing_live_flag": {
        "task": "meta_026_fixture_missing_live_flag",
        "approval_required": True,
        "shadow_mode_only": True,
    },
    "true_live_flag": {
        "task": "meta_026_fixture_true_live_flag",
        "live_trading_changes": True,
        "approval_required": True,
        "shadow_mode_only": True,
    },
    "non_boolean_live_flag": {
        "task": "meta_026_fixture_non_boolean_live_flag",
        "live_trading_changes": "false",
        "approval_required": True,
        "shadow_mode_only": True,
    },
    "malformed_payload": "{not-json",
}


def classify_live_trading_flag(payload: dict[str, Any] | None) -> tuple[str, list[str]]:
    """Classify the live-trading guardrail without allowing truthy coercion."""
    if payload is None:
        return "unexpected", ["payload_malformed"]

    if "live_trading_changes" not in payload:
        return "unexpected", ["live_trading_changes_missing"]

    value = payload["live_trading_changes"]
    if value is False:
        return "false", []

    if value is True:
        return "unexpected", ["live_trading_changes_true"]

    return "unexpected", [f"live_trading_changes_non_boolean:{type(value).__name__}"]


def validate_shadow_payload(raw_payload: Any) -> dict[str, Any]:
    """Return a read-only operator validation summary for a decoded/raw payload."""
    parse_errors: list[str] = []
    payload: dict[str, Any] | None

    if isinstance(raw_payload, str):
        try:
            decoded = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            payload = None
            parse_errors.append(f"json_decode_error:{exc.msg}")
        else:
            payload = decoded if isinstance(decoded, dict) else None
            if payload is None:
                parse_errors.append(f"payload_not_object:{type(decoded).__name__}")
    elif isinstance(raw_payload, dict):
        payload = raw_payload
    else:
        payload = None
        parse_errors.append(f"payload_not_object:{type(raw_payload).__name__}")

    live_flag, blockers = classify_live_trading_flag(payload)
    blockers = parse_errors + blockers
    if not blockers:
        blockers = ["explicit_user_approval_missing", "shadow_mode_not_integrated"]

    approval_required = True if payload is None else payload.get("approval_required") is not False
    shadow_mode_only = True if payload is None else payload.get("shadow_mode_only") is not False

    summary = {
        "task": "meta_026",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Validate shadow payload live_trading_changes guardrail.",
        "mode": "shadow_payload_validation_fixture",
        "payload_valid_json_object": payload is not None,
        "live_trading_changes": live_flag,
        "approval_required": approval_required,
        "shadow_mode_only": shadow_mode_only,
        "enforcement_safe": False,
        "order_mutation_allowed": False,
        "risk_logic_mutation_allowed": False,
        "safe_fallback": "base_strategy_unchanged",
        "blockers": blockers,
        "operator_summary": {
            "status": "BLOCKED_FOR_LIVE_ENFORCEMENT",
            "line": render_validation_line(
                enforcement_safe=False,
                live_trading_changes=live_flag,
                approval_required=approval_required,
                shadow_mode_only=shadow_mode_only,
                blocker_count=len(blockers),
            ),
        },
        "recommended_next_task": "meta_027_shadow_payload_wrapper_smoke",
        "live_trading_logic_changed": False,
    }
    return summary


def render_validation_line(
    *,
    enforcement_safe: bool,
    live_trading_changes: str,
    approval_required: bool,
    shadow_mode_only: bool,
    blocker_count: int,
) -> str:
    return (
        "meta_labeling_shadow_payload_validation "
        f"enforcement_safe={str(enforcement_safe).lower()} "
        f"live_trading_changes={live_trading_changes} "
        f"approval_required={str(approval_required).lower()} "
        f"shadow_mode_only={str(shadow_mode_only).lower()} "
        f"blockers={blocker_count}"
    )


def render_text(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            summary["operator_summary"]["line"],
            f"status={summary['operator_summary']['status']}",
            f"safe_fallback={summary['safe_fallback']}",
            "blockers=" + ",".join(summary["blockers"]),
            f"live_trading_changes={summary['live_trading_changes']}",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=tuple(FIXTURES), default="missing_live_flag")
    parser.add_argument("--payload", help="Raw JSON payload to validate instead of a built-in fixture.")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw_payload = args.payload if args.payload is not None else FIXTURES[args.fixture]
    summary = validate_shadow_payload(raw_payload)
    if args.format == "text":
        print(render_text(summary))
    else:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
