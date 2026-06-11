#!/usr/bin/env python3
"""Read-only cron/operator smoke wrapper for compact meta-label safety summaries.

This script demonstrates how cron/reporting can consume
``meta_058_daily_health_report.py --safety-summary json`` without touching live
trading, risk logic, model artifacts, config, or the trading database.

Default behavior is report-only and exits 0 even when enforcement remains unsafe,
so scheduled telemetry jobs do not create noisy failures for expected shadow-mode
blockers. Use ``--strict-alert-exit`` only in an operator smoke test if a non-zero
exit should mark unsafe promotion conditions.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_SCRIPT = ROOT / "self_learn" / "scripts" / "meta_058_daily_health_report.py"


def load_compact_safety_summary(
    *,
    report_script: Path = DEFAULT_REPORT_SCRIPT,
    python: str = sys.executable,
    days: float = 1.0,
    min_eligible_outcomes: int = 100,
) -> dict[str, Any]:
    """Run the read-only compact summary command and parse its JSON payload."""
    if not report_script.exists():
        raise FileNotFoundError(2, "compact report script not found", str(report_script))
    cmd = [
        python,
        str(report_script),
        "--days",
        str(days),
        "--min-eligible-outcomes",
        str(int(min_eligible_outcomes)),
        "--safety-summary",
        "json",
    ]
    completed = subprocess.run(cmd, check=True, text=True, capture_output=True)
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise ValueError("compact safety summary did not return a JSON object")
    return payload


def classify_alert(payload: dict[str, Any]) -> str:
    """Map the safety payload to a conservative operator-facing alert level."""
    payload_error = payload.get("payload_error")
    if isinstance(payload_error, str) and payload_error.startswith("report_launch_failed"):
        return "critical_compact_report_launch_failed"
    if isinstance(payload_error, str) and payload_error.startswith("report_subprocess_failed"):
        return "critical_compact_report_failed"
    if payload_error:
        return "critical_compact_payload_malformed"
    if payload.get("live_trading_changes") is not False:
        return "critical_live_trading_flag_unexpected"
    if not payload.get("ok", False):
        return "warning_summary_not_ok"
    if payload.get("enforcement_safe", False):
        return "info_enforcement_can_be_considered_after_separate_approval"
    return "expected_shadow_blocked"


def _format_payload_bool(value: Any) -> str:
    """Format a payload boolean without hiding unexpected non-false safety flags."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unexpected"


def format_cron_line(payload: dict[str, Any]) -> str:
    """Return a single-line status suitable for cron mail, logs, or chat summaries."""
    blockers = ",".join(str(item) for item in payload.get("blockers") or []) or "none"
    warnings = ",".join(str(item) for item in payload.get("warnings") or []) or "none"
    eligible = f"{int(payload.get('eligible_real_source_count', 0) or 0)}/{int(payload.get('required_eligible_outcomes', 0) or 0)}"
    return (
        "META_LABEL_CRON_CONSUMER "
        f"alert={classify_alert(payload)} "
        f"ok={str(bool(payload.get('ok'))).lower()} "
        f"enforcement_safe={str(bool(payload.get('enforcement_safe'))).lower()} "
        f"real_source_verified={str(bool(payload.get('real_source_verified'))).lower()} "
        f"eligible_real_source_count={eligible} "
        f"recommendation={payload.get('recommendation') or 'none'} "
        f"blockers={blockers} "
        f"warnings={warnings} "
        f"live_trading_changes={_format_payload_bool(payload.get('live_trading_changes'))}"
    )


def malformed_payload(reason: str) -> dict[str, Any]:
    """Return a conservative synthetic payload when compact JSON is malformed."""
    return {
        "ok": False,
        "live_trading_changes": None,
        "enforcement_safe": False,
        "real_source_verified": False,
        "eligible_real_source_count": 0,
        "required_eligible_outcomes": 0,
        "recommendation": "keep_meta_label_enforcement_disabled",
        "blockers": [f"compact_safety_payload_malformed:{reason}"],
        "warnings": [],
        "payload_error": reason,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-script", type=Path, default=DEFAULT_REPORT_SCRIPT)
    parser.add_argument("--python", default=sys.executable, help="Python executable used to invoke the report script")
    parser.add_argument("--days", type=float, default=1.0)
    parser.add_argument("--min-eligible-outcomes", type=int, default=100)
    parser.add_argument(
        "--strict-alert-exit",
        action="store_true",
        help="exit 2 when the parsed payload is not ok or enforcement is not safe; default stays report-only",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = load_compact_safety_summary(
            report_script=args.report_script,
            python=args.python,
            days=args.days,
            min_eligible_outcomes=args.min_eligible_outcomes,
        )
    except json.JSONDecodeError:
        payload = malformed_payload("invalid_json")
    except ValueError:
        payload = malformed_payload("non_object_json")
    except subprocess.CalledProcessError as exc:
        payload = malformed_payload(f"report_subprocess_failed:exit_{exc.returncode}")
    except FileNotFoundError as exc:
        payload = malformed_payload(f"report_launch_failed:missing_script:{Path(exc.filename).name if exc.filename else 'unknown'}")
    except OSError as exc:
        payload = malformed_payload(f"report_launch_failed:os_error:{exc.__class__.__name__}")
    print(format_cron_line(payload))
    if args.strict_alert_exit and (
        payload.get("live_trading_changes") is not False
        or not payload.get("ok", False)
        or not payload.get("enforcement_safe", False)
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
