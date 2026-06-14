import json
import subprocess
import sys
from pathlib import Path

from dev.meta_labeling.scripts.meta_026_shadow_payload_validation_fixture import (
    FIXTURES,
    render_text,
    validate_shadow_payload,
)

SCRIPT = Path("dev/meta_labeling/scripts/meta_026_shadow_payload_validation_fixture.py")


def test_missing_live_trading_changes_is_unexpected_and_not_safe():
    summary = validate_shadow_payload(FIXTURES["missing_live_flag"])

    assert summary["task"] == "meta_026"
    assert summary["enforcement_safe"] is False
    assert summary["live_trading_changes"] == "unexpected"
    assert "live_trading_changes_missing" in summary["blockers"]
    assert summary["order_mutation_allowed"] is False
    assert summary["risk_logic_mutation_allowed"] is False


def test_malformed_payload_is_unexpected_and_not_safe():
    summary = validate_shadow_payload(FIXTURES["malformed_payload"])

    assert summary["payload_valid_json_object"] is False
    assert summary["enforcement_safe"] is False
    assert summary["live_trading_changes"] == "unexpected"
    assert any(blocker.startswith("json_decode_error:") for blocker in summary["blockers"])


def test_non_boolean_live_trading_changes_is_unexpected_and_not_safe():
    summary = validate_shadow_payload(FIXTURES["non_boolean_live_flag"])

    assert summary["enforcement_safe"] is False
    assert summary["live_trading_changes"] == "unexpected"
    assert "live_trading_changes_non_boolean:str" in summary["blockers"]


def test_true_live_trading_changes_is_unexpected_and_not_safe():
    summary = validate_shadow_payload(FIXTURES["true_live_flag"])

    assert summary["enforcement_safe"] is False
    assert summary["live_trading_changes"] == "unexpected"
    assert "live_trading_changes_true" in summary["blockers"]


def test_explicit_false_live_trading_changes_remains_shadow_blocked():
    summary = validate_shadow_payload(FIXTURES["valid_false"])

    assert summary["live_trading_changes"] == "false"
    assert summary["enforcement_safe"] is False
    assert "explicit_user_approval_missing" in summary["blockers"]
    assert summary["operator_summary"]["line"].startswith(
        "meta_labeling_shadow_payload_validation "
    )


def test_text_render_exposes_unexpected_live_trading_flag():
    text = render_text(validate_shadow_payload(FIXTURES["true_live_flag"]))

    assert "enforcement_safe=false" in text
    assert "live_trading_changes=unexpected" in text
    assert "status=BLOCKED_FOR_LIVE_ENFORCEMENT" in text


def test_cli_json_fixture_outputs_unexpected_for_missing_flag():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--fixture", "missing_live_flag", "--format", "json"],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert payload["task"] == "meta_026"
    assert payload["enforcement_safe"] is False
    assert payload["live_trading_changes"] == "unexpected"
    assert payload["recommended_next_task"] == "meta_027_shadow_payload_wrapper_smoke"


def test_cli_text_payload_keeps_false_visible_but_not_enforced():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--payload",
            '{"live_trading_changes": false}',
            "--format",
            "text",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    first_line = result.stdout.splitlines()[0]
    assert "enforcement_safe=false" in first_line
    assert "live_trading_changes=false" in first_line
