"""Runtime instance guard using a PID file with stale-process detection.

Prevents two live trading launchers from running simultaneously, which would
cause double-order and duplicate position-mutation bugs.

Usage::

    lock = RuntimeLock()
    if not lock.acquire():
        sys.exit("Another launcher is already running.")
    try:
        run_live_loop()
    finally:
        lock.release()
"""
from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_PID_FILE = Path(__file__).parent.parent.parent / "v3_pid.txt"


class RuntimeLock:
    """File-based single-instance guard with stale-PID recovery."""

    def __init__(self, pid_file: "Path | str | None" = None) -> None:
        self.pid_file = Path(pid_file) if pid_file else _DEFAULT_PID_FILE
        self._acquired = False

    def _read_existing_pid(self) -> "int | None":
        try:
            return int(self.pid_file.read_text().strip())
        except (FileNotFoundError, ValueError, OSError):
            return None

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we lack permission to signal it — treat as alive.
            return True

    def acquire(self) -> bool:
        """Try to acquire the runtime lock.

        Returns True if the lock was acquired (no live owner or stale PID).
        Returns False if another live process already holds the lock.
        """
        existing_pid = self._read_existing_pid()
        if existing_pid is not None and self._is_process_alive(existing_pid):
            return False
        # Stale lock or no lock file — write our PID.
        try:
            self.pid_file.write_text(str(os.getpid()))
        except OSError:
            return False
        self._acquired = True
        return True

    def release(self) -> None:
        """Release the lock (best-effort; safe to call multiple times)."""
        if self._acquired:
            try:
                self.pid_file.unlink(missing_ok=True)
            except OSError:
                pass
            self._acquired = False

    def __enter__(self) -> "RuntimeLock":
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
