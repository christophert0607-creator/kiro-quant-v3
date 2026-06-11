import importlib.util
import json
import sys
from pathlib import Path

_SMOKE_PATH = Path(__file__).resolve().parents[1] / "self_learn" / "scripts" / "meta_064_cron_safety_summary_smoke.py"
_spec = importlib.util.spec_from_file_location("meta_064_cron_safety_summary_smoke_under_test", _SMOKE_PATH)
assert _spec and _spec.loader
_smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_smoke)


def _fake_report_script(tmp_path: Path, payload: dict) -> Path:
    script = tmp_path / "fake_meta_058_report.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        f"print({json.dumps(json.dumps(payload))})\n",
        encoding="utf-8",
    )
    return script


def _fake_report_script_output(tmp_path: Path, output: str) -> Path:
    script = tmp_path / "fake_meta_058_report.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        f"print({json.dumps(output)})\n",
        encoding="utf-8",
    )
    return script


def _fake_report_script_failure(tmp_path: Path, exit_code: int = 7) -> Path:
    script = tmp_path / "fake_meta_058_report.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('simulated compact report failure', file=sys.stderr)\n"
        f"raise SystemExit({int(exit_code)})\n",
        encoding="utf-8",
    )
    return script


def test_cron_consumer_formats_expected_shadow_blocked_summary(tmp_path, capsys):
    payload = {
        "ok": True,
        "live_trading_changes": False,
        "enforcement_safe": False,
        "recommendation": "keep_meta_label_enforcement_disabled",
        "blockers": ["insufficient_eligible_real_outcomes:0/100"],
        "warnings": ["meta_label_no_data_events:5"],
        "eligible_real_source_count": 0,
        "required_eligible_outcomes": 100,
        "real_source_verified": False,
    }
    report_script = _fake_report_script(tmp_path, payload)

    exit_code = _smoke.main(["--report-script", str(report_script), "--python", sys.executable])

    out = capsys.readouterr().out.strip()
    assert exit_code == 0
    assert out.startswith("META_LABEL_CRON_CONSUMER alert=expected_shadow_blocked ")
    assert "enforcement_safe=false" in out
    assert "eligible_real_source_count=0/100" in out
    assert "blockers=insufficient_eligible_real_outcomes:0/100" in out
    assert out.endswith("live_trading_changes=false")


def test_cron_consumer_strict_alert_exit_is_opt_in(tmp_path, capsys):
    payload = {
        "ok": True,
        "live_trading_changes": False,
        "enforcement_safe": False,
        "recommendation": "keep_meta_label_enforcement_disabled",
        "blockers": [],
        "warnings": [],
        "eligible_real_source_count": 0,
        "required_eligible_outcomes": 2,
        "real_source_verified": False,
    }
    report_script = _fake_report_script(tmp_path, payload)

    exit_code = _smoke.main([
        "--report-script",
        str(report_script),
        "--python",
        sys.executable,
        "--strict-alert-exit",
    ])

    assert exit_code == 2
    assert "alert=expected_shadow_blocked" in capsys.readouterr().out


def test_cron_consumer_classifies_safe_consideration_as_info():
    payload = {
        "ok": True,
        "live_trading_changes": False,
        "enforcement_safe": True,
        "recommendation": "meta-label enforcement can be considered after separate model-quality approval",
        "blockers": [],
        "warnings": [],
        "eligible_real_source_count": 100,
        "required_eligible_outcomes": 100,
        "real_source_verified": True,
    }

    assert _smoke.classify_alert(payload) == "info_enforcement_can_be_considered_after_separate_approval"
    line = _smoke.format_cron_line(payload)
    assert "alert=info_enforcement_can_be_considered_after_separate_approval" in line
    assert "real_source_verified=true" in line
    assert "live_trading_changes=false" in line


def test_cron_consumer_does_not_hide_unexpected_live_trading_change_flag(tmp_path, capsys):
    payload = {
        "ok": True,
        "live_trading_changes": True,
        "enforcement_safe": True,
        "recommendation": "meta-label enforcement can be considered after separate model-quality approval",
        "blockers": [],
        "warnings": [],
        "eligible_real_source_count": 100,
        "required_eligible_outcomes": 100,
        "real_source_verified": True,
    }
    report_script = _fake_report_script(tmp_path, payload)

    exit_code = _smoke.main([
        "--report-script",
        str(report_script),
        "--python",
        sys.executable,
        "--strict-alert-exit",
    ])

    out = capsys.readouterr().out.strip()
    assert exit_code == 2
    assert "alert=critical_live_trading_flag_unexpected" in out
    assert out.endswith("live_trading_changes=true")


def test_cron_consumer_treats_missing_live_trading_change_flag_as_critical(tmp_path, capsys):
    payload = {
        "ok": True,
        "enforcement_safe": True,
        "recommendation": "meta-label enforcement can be considered after separate model-quality approval",
        "blockers": [],
        "warnings": [],
        "eligible_real_source_count": 100,
        "required_eligible_outcomes": 100,
        "real_source_verified": True,
    }
    report_script = _fake_report_script(tmp_path, payload)

    exit_code = _smoke.main([
        "--report-script",
        str(report_script),
        "--python",
        sys.executable,
        "--strict-alert-exit",
    ])

    out = capsys.readouterr().out.strip()
    assert exit_code == 2
    assert "alert=critical_live_trading_flag_unexpected" in out
    assert out.endswith("live_trading_changes=unexpected")


def test_cron_consumer_treats_non_boolean_live_trading_change_flag_as_critical(tmp_path, capsys):
    payload = {
        "ok": True,
        "live_trading_changes": "false",
        "enforcement_safe": True,
        "recommendation": "meta-label enforcement can be considered after separate model-quality approval",
        "blockers": [],
        "warnings": [],
        "eligible_real_source_count": 100,
        "required_eligible_outcomes": 100,
        "real_source_verified": True,
    }
    report_script = _fake_report_script(tmp_path, payload)

    exit_code = _smoke.main([
        "--report-script",
        str(report_script),
        "--python",
        sys.executable,
        "--strict-alert-exit",
    ])

    out = capsys.readouterr().out.strip()
    assert exit_code == 2
    assert "alert=critical_live_trading_flag_unexpected" in out
    assert out.endswith("live_trading_changes=unexpected")


def test_cron_consumer_treats_non_object_compact_payload_as_malformed(tmp_path, capsys):
    report_script = _fake_report_script_output(tmp_path, '["not", "an", "object"]')

    exit_code = _smoke.main([
        "--report-script",
        str(report_script),
        "--python",
        sys.executable,
        "--strict-alert-exit",
    ])

    out = capsys.readouterr().out.strip()
    assert exit_code == 2
    assert "alert=critical_compact_payload_malformed" in out
    assert "blockers=compact_safety_payload_malformed:non_object_json" in out
    assert out.endswith("live_trading_changes=unexpected")


def test_cron_consumer_treats_invalid_compact_json_as_malformed(tmp_path, capsys):
    report_script = _fake_report_script_output(tmp_path, "{not valid json")

    exit_code = _smoke.main([
        "--report-script",
        str(report_script),
        "--python",
        sys.executable,
        "--strict-alert-exit",
    ])

    out = capsys.readouterr().out.strip()
    assert exit_code == 2
    assert "alert=critical_compact_payload_malformed" in out
    assert "blockers=compact_safety_payload_malformed:invalid_json" in out
    assert out.endswith("live_trading_changes=unexpected")


def test_cron_consumer_treats_report_subprocess_failure_as_critical(tmp_path, capsys):
    report_script = _fake_report_script_failure(tmp_path, exit_code=7)

    exit_code = _smoke.main([
        "--report-script",
        str(report_script),
        "--python",
        sys.executable,
        "--strict-alert-exit",
    ])

    out = capsys.readouterr().out.strip()
    assert exit_code == 2
    assert "alert=critical_compact_report_failed" in out
    assert "blockers=compact_safety_payload_malformed:report_subprocess_failed:exit_7" in out
    assert "enforcement_safe=false" in out
    assert out.endswith("live_trading_changes=unexpected")


def test_cron_consumer_treats_missing_report_script_as_launch_failure(tmp_path, capsys):
    missing_report_script = tmp_path / "missing_meta_058_report.py"

    exit_code = _smoke.main([
        "--report-script",
        str(missing_report_script),
        "--python",
        sys.executable,
        "--strict-alert-exit",
    ])

    out = capsys.readouterr().out.strip()
    assert exit_code == 2
    assert "alert=critical_compact_report_launch_failed" in out
    assert "blockers=compact_safety_payload_malformed:report_launch_failed:missing_script:missing_meta_058_report.py" in out
    assert "enforcement_safe=false" in out
    assert out.endswith("live_trading_changes=unexpected")


def test_cron_consumer_treats_missing_python_executable_as_launch_failure(tmp_path, capsys):
    report_script = _fake_report_script(tmp_path, {"ok": True, "live_trading_changes": False})
    missing_python = tmp_path / "missing-python"

    exit_code = _smoke.main([
        "--report-script",
        str(report_script),
        "--python",
        str(missing_python),
        "--strict-alert-exit",
    ])

    out = capsys.readouterr().out.strip()
    assert exit_code == 2
    assert "alert=critical_compact_report_launch_failed" in out
    assert "blockers=compact_safety_payload_malformed:report_launch_failed:missing_script:missing-python" in out
    assert out.endswith("live_trading_changes=unexpected")
