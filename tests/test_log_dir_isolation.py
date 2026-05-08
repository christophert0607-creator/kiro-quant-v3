"""
Tests for KIRO_LOG_DIR log-path isolation.

Production log files (v3_live.log, decisions.jsonl, idle_scheduler.log) must
never be written to during test runs.  The conftest.py sets KIRO_LOG_DIR before
any test module is imported so every RotatingFileHandler uses the temp directory.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path


# ── _get_log_dir helpers ──────────────────────────────────────────────────────

class TestGetLogDir:
    def test_default_is_repo_logs(self, tmp_path, monkeypatch):
        """Without KIRO_LOG_DIR the function returns <project_root>/logs."""
        monkeypatch.delenv("KIRO_LOG_DIR", raising=False)
        from v3_pipeline.core.main_loop import _get_log_dir
        result = _get_log_dir()
        assert result.name == "logs"
        assert result.is_dir()

    def test_env_override_is_honoured(self, tmp_path, monkeypatch):
        """With KIRO_LOG_DIR set, _get_log_dir returns that path."""
        custom = tmp_path / "my_logs"
        monkeypatch.setenv("KIRO_LOG_DIR", str(custom))
        from v3_pipeline.core.main_loop import _get_log_dir
        result = _get_log_dir()
        assert result == custom
        assert custom.is_dir()

    def test_env_override_creates_directory(self, tmp_path, monkeypatch):
        """_get_log_dir creates the directory if it does not exist yet."""
        custom = tmp_path / "deep" / "nested" / "logs"
        assert not custom.exists()
        monkeypatch.setenv("KIRO_LOG_DIR", str(custom))
        from v3_pipeline.core.main_loop import _get_log_dir
        _get_log_dir()
        assert custom.is_dir()


class TestIdleGetLogDir:
    def test_default_is_project_logs(self, tmp_path, monkeypatch):
        """Without KIRO_LOG_DIR, idle logger uses <project_root>/logs."""
        monkeypatch.delenv("KIRO_LOG_DIR", raising=False)
        from idle_task_scheduler import _get_idle_log_dir
        result = _get_idle_log_dir()
        assert result.name == "logs"
        assert result.is_dir()

    def test_env_override_is_honoured(self, tmp_path, monkeypatch):
        """With KIRO_LOG_DIR set, idle logger uses that path."""
        custom = tmp_path / "idle_logs"
        monkeypatch.setenv("KIRO_LOG_DIR", str(custom))
        from idle_task_scheduler import _get_idle_log_dir
        result = _get_idle_log_dir()
        assert result == custom
        assert custom.is_dir()


# ── Conftest sets KIRO_LOG_DIR ────────────────────────────────────────────────

class TestConftestLogIsolation:
    def test_kiro_log_dir_is_set(self):
        """conftest.pytest_configure sets KIRO_LOG_DIR to a temp directory."""
        log_dir = os.environ.get("KIRO_LOG_DIR")
        assert log_dir is not None, "KIRO_LOG_DIR must be set by conftest"
        assert Path(log_dir).is_dir()

    def test_kiro_log_dir_is_not_production(self):
        """KIRO_LOG_DIR must not point at the production logs/ directory."""
        log_dir = Path(os.environ.get("KIRO_LOG_DIR", ""))
        prod_logs = Path(__file__).parent.parent / "logs"
        assert log_dir != prod_logs, (
            "KIRO_LOG_DIR points at the production logs directory — "
            "test logs would pollute production"
        )

    def test_kiro_log_dir_is_temp(self):
        """KIRO_LOG_DIR should be under the system temp directory."""
        log_dir = Path(os.environ.get("KIRO_LOG_DIR", ""))
        tmp = Path(tempfile.gettempdir())
        assert log_dir.is_relative_to(tmp), (
            f"Expected KIRO_LOG_DIR under {tmp}, got {log_dir}"
        )
