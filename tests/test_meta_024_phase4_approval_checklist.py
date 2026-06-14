import json
import subprocess
import sys
from pathlib import Path

from dev.meta_labeling.scripts.meta_024_phase4_approval_checklist import build_checklist, write_markdown


SCRIPT = Path("dev/meta_labeling/scripts/meta_024_phase4_approval_checklist.py")


def test_build_checklist_requires_approval_and_no_live_changes():
    report = build_checklist()

    assert report["task"] == "meta_024"
    assert report["approval_required_before_live_integration"] is True
    assert report["live_trading_changes"] is False
    assert "meta_025_shadow_mode_summary_fixture" == report["recommended_next_task"]

    gate_ids = {item["id"] for item in report["approval_gates"]}
    assert "gate_user_approval" in gate_ids
    assert "gate_shadow_first" in gate_ids
    assert "gate_metric_bundle" in gate_ids

    blocked = "\n".join(report["blocked_without_user_approval"])
    assert "main_loop.py" in blocked
    assert "order routing" in blocked
    assert "risk checks" in blocked


def test_cli_emits_json_with_operator_safety_fields():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--format", "json"],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert payload["approval_required_before_live_integration"] is True
    assert payload["live_trading_changes"] is False
    assert len(payload["rollback_triggers"]) >= 3
    assert any(item["id"] == "rollback_payload_integrity" for item in payload["rollback_triggers"])


def test_write_markdown_contains_approval_and_rollback_sections(tmp_path):
    report = build_checklist()
    out = tmp_path / "checklist.md"
    write_markdown(report, out)

    text = out.read_text(encoding="utf-8")
    assert "Meta-Labeling Phase 4 Approval Checklist" in text
    assert "Approval gates" in text
    assert "Shadow-mode guardrails" in text
    assert "Rollback triggers" in text
    assert "Live trading/risk logic changed: `False`" in text
