"""Acceptance tests for runtime single-instance lock.

Acceptance criteria:
- Second launcher cannot enter live trading loop when lock is held
- Stale PID lock (process no longer alive) can be recovered
- Lock acquisition writes current PID to pid file
- Lock release removes the pid file
- Context manager form works (acquire/release)
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from v3_pipeline.core.runtime_lock import RuntimeLock


# ── Acquire / release basics ──────────────────────────────────────────────────


def test_acquire_creates_pid_file(tmp_path):
    lock = RuntimeLock(pid_file=tmp_path / "test.pid")
    assert lock.acquire()
    assert (tmp_path / "test.pid").exists()
    lock.release()


def test_acquire_writes_current_pid(tmp_path):
    lock = RuntimeLock(pid_file=tmp_path / "test.pid")
    lock.acquire()
    written = int((tmp_path / "test.pid").read_text().strip())
    assert written == os.getpid()
    lock.release()


def test_release_removes_pid_file(tmp_path):
    lock = RuntimeLock(pid_file=tmp_path / "test.pid")
    lock.acquire()
    lock.release()
    assert not (tmp_path / "test.pid").exists()


def test_release_is_idempotent(tmp_path):
    lock = RuntimeLock(pid_file=tmp_path / "test.pid")
    lock.acquire()
    lock.release()
    lock.release()  # second call must not raise


# ── Second launcher blocked ───────────────────────────────────────────────────


def test_second_launcher_blocked_when_lock_held(tmp_path):
    """First lock acquired; second acquire must return False."""
    lock1 = RuntimeLock(pid_file=tmp_path / "test.pid")
    lock2 = RuntimeLock(pid_file=tmp_path / "test.pid")

    assert lock1.acquire()
    # Simulate lock2 checking: current process (holding lock1) is alive
    assert lock2.acquire() is False, "Second launcher must be blocked"
    lock1.release()


def test_second_launcher_blocked_returns_false_not_raises(tmp_path):
    lock1 = RuntimeLock(pid_file=tmp_path / "test.pid")
    lock2 = RuntimeLock(pid_file=tmp_path / "test.pid")
    lock1.acquire()
    result = lock2.acquire()
    assert result is False
    lock1.release()


# ── Stale PID recovery ────────────────────────────────────────────────────────


def test_stale_pid_lock_can_be_recovered(tmp_path):
    """PID file with a dead process should be overwritten (stale recovery)."""
    pid_file = tmp_path / "test.pid"
    dead_pid = 999999999  # Almost certainly not a real process
    pid_file.write_text(str(dead_pid))

    lock = RuntimeLock(pid_file=pid_file)
    assert lock.acquire(), "Stale lock with dead PID must be recoverable"
    assert int(pid_file.read_text().strip()) == os.getpid()
    lock.release()


def test_missing_pid_file_is_treated_as_no_lock(tmp_path):
    pid_file = tmp_path / "nonexistent.pid"
    lock = RuntimeLock(pid_file=pid_file)
    assert lock.acquire()
    lock.release()


def test_corrupted_pid_file_is_treated_as_stale(tmp_path):
    pid_file = tmp_path / "test.pid"
    pid_file.write_text("not_a_number")
    lock = RuntimeLock(pid_file=pid_file)
    assert lock.acquire(), "Corrupted PID file must be treated as stale"
    lock.release()


# ── Context manager ───────────────────────────────────────────────────────────


def test_context_manager_acquires_and_releases(tmp_path):
    pid_file = tmp_path / "test.pid"
    lock = RuntimeLock(pid_file=pid_file)
    assert lock.acquire()
    with lock:
        assert pid_file.exists()
    assert not pid_file.exists()


def test_context_manager_releases_on_exception(tmp_path):
    pid_file = tmp_path / "test.pid"
    lock = RuntimeLock(pid_file=pid_file)
    lock.acquire()
    try:
        with lock:
            raise RuntimeError("simulated crash")
    except RuntimeError:
        pass
    assert not pid_file.exists(), "Lock must be released even when exception is raised"


# ── v3_launcher integration ───────────────────────────────────────────────────


def test_run_kiro_v35_exits_early_when_lock_already_held(tmp_path):
    """run_kiro_v35 must return early without entering the live loop when locked."""
    import sys
    from types import SimpleNamespace

    # Pre-write a PID file with the CURRENT process's PID so it looks like
    # another (alive) launcher is running.
    pid_file = tmp_path / "v3_pid.txt"
    pid_file.write_text(str(os.getpid()))

    import v3_launcher as launcher_mod

    loop_started = []

    with (
        patch.object(
            sys.modules["v3_pipeline.core.runtime_lock"],
            "_DEFAULT_PID_FILE",
            pid_file,
        ),
        patch("v3_launcher.build_live_config", side_effect=lambda *a: loop_started.append(True)),
    ):
        from v3_pipeline.core.runtime_lock import RuntimeLock as RL

        # Patch RuntimeLock to use our tmp pid_file
        original_init = RL.__init__

        def patched_init(self, pid_file=None):
            original_init(self, pid_file=pid_file or str(tmp_path / "v3_pid.txt"))

        with patch.object(RL, "__init__", patched_init):
            launcher_mod.run_kiro_v35(config_path="config.json")

    assert loop_started == [], "build_live_config must not be called when lock is already held"
