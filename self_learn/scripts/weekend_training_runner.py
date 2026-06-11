#!/usr/bin/env python3
"""Weekend 24h training runner for Kiro Quant V3.

Safe operating rules:
- Collector/progress are read-mostly and append PROGRESS.md only.
- Deep mode is weekend-only unless --force is used.
- Deep mode refuses to run when provenance guard is blocked; synthetic-only data is
  reported as a blocker, not promoted.
- --dry-run / --dry-run-progress never writes model artifacts; --dry-run-progress
  also avoids writing PROGRESS.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from self_learn.scripts.weekend_training_status import MODE, build_status
except Exception:  # pragma: no cover - direct script launch fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from self_learn.scripts.weekend_training_status import MODE, build_status

ROOT = Path(__file__).resolve().parents[2]
PROGRESS_FILE = Path("self_learn") / "PROGRESS.md"

CommandRunner = Callable[..., dict[str, Any]]
LiveRuntimeChecker = Callable[[], bool]


def _fmt_metric(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _risk_label(status: dict[str, Any]) -> str:
    guard = status.get("guard") if isinstance(status.get("guard"), dict) else {}
    market_data = status.get("market_data") if isinstance(status.get("market_data"), dict) else {}
    eligible = int(status.get("eligible_real_source_count") or 0)
    if not status.get("schema_ready"):
        return "schema_blocked"
    if eligible == 0:
        return "synthetic_only"
    if guard.get("status") != "pass":
        return "data_gap"
    if int(market_data.get("market_data_rows") or 0) <= 0:
        return "market_data_gap"
    return "guarded_ready"


def render_progress_block(
    status: dict[str, Any],
    cycle: str,
    steps: list[dict[str, Any]] | None = None,
    reason: str | None = None,
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or datetime.now(timezone.utc)
    stats = status.get("stats") if isinstance(status.get("stats"), dict) else {}
    guard = status.get("guard") if isinstance(status.get("guard"), dict) else {}
    latest = status.get("latest_metrics") if isinstance(status.get("latest_metrics"), dict) else None
    metrics = latest.get("metrics", {}) if isinstance(latest, dict) and isinstance(latest.get("metrics"), dict) else {}
    artifacts = "written=false"
    if latest and latest.get("model_path") and guard.get("status") == "pass":
        artifacts = f"written=true | model_path={latest.get('model_path')}"
    step_text = ", ".join(f"{s.get('name')}={s.get('status')}" for s in (steps or [])) or "status=ok"
    next_action = reason or guard.get("reason") or "next scheduled step"
    return (
        f"\n## [{generated_at.astimezone().strftime('%Y-%m-%d %H:%M %Z')}] Weekend Training Cycle\n"
        f"**Mode:** {status.get('mode', MODE)}\n"
        f"**Cycle:** {cycle}\n"
        f"**DB:** predictions={stats.get('predictions', 0)} | signals={stats.get('signals', 0)} | "
        f"closed={stats.get('closed', 0)} | outcomes={stats.get('outcomes', 0)}\n"
        f"**Real Eligible Outcomes:** {status.get('eligible_real_source_count', 0)} / "
        f"required={guard.get('required_eligible_outcomes', 100)} | source_verified={str(guard.get('real_source_verified', False)).lower()}\n"
        f"**Retrain Guard:** {guard.get('status', 'blocked')} | reason={guard.get('reason') or 'pass'}\n"
        f"**Metrics:** accuracy={_fmt_metric(metrics.get('accuracy'))} | win_rate={_fmt_metric(metrics.get('win_rate'))} | "
        f"samples={_fmt_metric(metrics.get('total_samples'))} | iterations={_fmt_metric(metrics.get('actual_iterations'))} | "
        f"early_stopped={_fmt_metric(metrics.get('early_stopped'))}\n"
        f"**Artifacts:** {artifacts}\n"
        f"**Steps:** {step_text}\n"
        f"**Risk:** {_risk_label(status)}\n"
        f"**Next:** {next_action}\n"
    )


def append_progress(workspace: str | Path, block: str) -> Path:
    path = Path(workspace) / PROGRESS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(block)
        if not block.endswith("\n"):
            fh.write("\n")
    return path


def is_weekend_training_window(now: datetime | None = None) -> bool:
    now = now or datetime.now().astimezone()
    # Python weekday: Monday=0, Saturday=5, Sunday=6.
    return now.weekday() in {5, 6}


def live_runtime_is_running() -> bool:
    # Weekend launcher is usually IDLE/COLLECT mode; do not let the mere presence
    # of v3_launcher.py block the planned weekend training guard/report loop.
    # On weekdays, be conservative and treat an active launcher process as busy.
    if is_weekend_training_window():
        return False
    try:
        proc = subprocess.run(["ps", "aux"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    except Exception:
        return False
    lines = [line for line in proc.stdout.splitlines() if "v3_launcher.py" in line and "grep" not in line]
    return bool(lines)


def run_command(workspace: str | Path, name: str, args: list[str], timeout: int = 1800) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(workspace),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return {
            "name": name,
            "status": "ok" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {"name": name, "status": "timeout", "returncode": None, "stdout": exc.stdout or "", "stderr": exc.stderr or ""}
    except Exception as exc:
        return {"name": name, "status": "error", "returncode": None, "stdout": "", "stderr": str(exc)}


def _deep_commands(dry_run: bool) -> list[tuple[str, list[str]]]:
    py = "/usr/bin/python3"
    commands = [
        ("backfill_indicators_dry_run", [py, "self_learn/backfill_indicators.py", "--dry-run"]),
        ("trade_outcome_head_dry_run", [py, "self_learn/scripts/train_trade_outcome_head.py", "--dry-run"]),
    ]
    if not dry_run:
        commands.append(("guarded_retrain", [py, "-m", "self_learn.retrain", "retrain"]))
    return commands


def run_cycle(
    workspace: str | Path = ROOT,
    mode: str = "collector",
    dry_run: bool = False,
    dry_run_progress: bool = False,
    force: bool = False,
    max_seconds: int = 1800,
    now: datetime | None = None,
    command_runner: CommandRunner | None = None,
    live_runtime_checker: LiveRuntimeChecker | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    command_runner = command_runner or run_command
    live_runtime_checker = live_runtime_checker or live_runtime_is_running
    status = build_status(workspace)
    steps: list[dict[str, Any]] = [{"name": "status", "status": "ok"}]

    result_status = "ok"
    reason = None
    if mode not in {"collector", "progress", "deep", "summary"}:
        result_status = "error"
        reason = f"invalid_mode:{mode}"
    elif mode == "deep":
        if not force and not is_weekend_training_window(now):
            result_status = "skipped"
            reason = "outside_weekend_training_window"
        elif live_runtime_checker() and not force:
            result_status = "skipped"
            reason = "live_runtime_busy"
        elif status.get("guard", {}).get("status") != "pass":
            result_status = "blocked"
            reason = status.get("guard", {}).get("reason") or "promotion_guard_blocked"
        else:
            for name, args in _deep_commands(dry_run=dry_run):
                step = command_runner(workspace, name, args, timeout=max_seconds)
                steps.append(step)
                if step.get("status") != "ok":
                    result_status = "error"
                    reason = f"step_failed:{name}"
                    break
    elif mode in {"progress", "summary"}:
        step = command_runner(
            workspace,
            "prediction_health",
            ["/usr/bin/python3", "scripts/report_prediction_health.py", "--days", "1"],
            timeout=min(max_seconds, 300),
        )
        steps.append(step)
        if step.get("status") != "ok":
            result_status = "error"
            reason = "prediction_health_failed"

    block = render_progress_block(status, cycle=mode, steps=steps, reason=reason)
    progress_path = None
    if not dry_run_progress:
        progress_path = append_progress(workspace, block)

    return {
        "status": result_status,
        "reason": reason,
        "mode": mode,
        "workspace": str(workspace),
        "progress_path": str(progress_path) if progress_path else None,
        "progress_block": block,
        "steps": steps,
        "guard": status.get("guard"),
        "risk": _risk_label(status),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one weekend 24h training cycle")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--mode", choices=["collector", "progress", "deep", "summary"], default="collector")
    parser.add_argument("--dry-run", action="store_true", help="Avoid guarded retrain persistence in deep mode")
    parser.add_argument("--dry-run-progress", action="store_true", help="Print progress block but do not append PROGRESS.md")
    parser.add_argument("--force", action="store_true", help="Override weekend/runtime-busy guards")
    parser.add_argument("--max-seconds", type=int, default=1800)
    args = parser.parse_args(argv)
    result = run_cycle(
        workspace=args.workspace,
        mode=args.mode,
        dry_run=args.dry_run,
        dry_run_progress=args.dry_run_progress,
        force=args.force,
        max_seconds=args.max_seconds,
    )
    print(result["progress_block"])
    print(json.dumps({k: v for k, v in result.items() if k != "progress_block"}, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] in {"ok", "blocked", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
