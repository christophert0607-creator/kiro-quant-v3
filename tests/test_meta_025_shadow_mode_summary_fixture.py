import json
import subprocess
import sys
from pathlib import Path

from dev.meta_labeling.scripts.meta_025_shadow_mode_summary_fixture import (
    build_summary,
    render_summary_line,
    render_text,
)


SCRIPT = Path("dev/meta_labeling/scripts/meta_025_shadow_mode_summary_fixture.py")


def test_build_summary_is_shadow_only_and_blocks_enforcement():
    summary = build_summary()

    assert summary["task"] == "meta_025"
    assert summary["shadow_mode_only"] is True
    assert summary["approval_required"] is True
    assert summary["enforcement_safe"] is False
    assert summary["live_trading_changes"] is False
    assert summary["order_mutation_allowed"] is False
    assert summary["risk_logic_mutation_allowed"] is False
    assert summary["safe_fallback"] == "base_strategy_unchanged"
    assert "explicit_user_approval_missing" in summary["blockers"]


def test_summary_line_exposes_required_operator_flags():
    line = render_summary_line(
        enforcement_safe=False,
        live_trading_changes=False,
        approval_required=True,
        shadow_mode_only=True,
        blocker_count=3,
    )

    assert line.startswith("meta_labeling_shadow_summary ")
    assert "enforcement_safe=false" in line
    assert "live_trading_changes=false" in line
    assert "approval_required=true" in line
    assert "shadow_mode_only=true" in line
    assert "blockers=3" in line


def test_text_render_keeps_live_trading_changes_visible():
    text = render_text(build_summary())

    assert "live_trading_changes=false" in text
    assert "status=BLOCKED_FOR_LIVE_ENFORCEMENT" in text
    assert "safe_fallback=base_strategy_unchanged" in text


def test_cli_emits_json_fixture_with_no_live_changes():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--format", "json"],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert payload["task"] == "meta_025"
    assert payload["enforcement_safe"] is False
    assert payload["live_trading_changes"] is False
    assert payload["approval_required"] is True
    assert payload["recommended_next_task"] == "meta_026_shadow_payload_validation_fixture"


def test_cli_text_contains_cron_friendly_one_liner():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--format", "text"],
        check=True,
        text=True,
        capture_output=True,
    )

    first_line = result.stdout.splitlines()[0]
    assert first_line.startswith("meta_labeling_shadow_summary ")
    assert "enforcement_safe=false" in first_line
    assert "live_trading_changes=false" in first_line
