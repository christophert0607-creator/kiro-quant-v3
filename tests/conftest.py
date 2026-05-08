import os
import shutil
import tempfile

import requests  # noqa: F401 — must be imported before test_main_loop_trade_bridge.py stubs it

collect_ignore = [
    "test_quant_v2_kline_cache.py",
    "test_risk_guard_net_assets.py",
    "test_data_manager.py",
]

# ── Log isolation ─────────────────────────────────────────────────────────────
# Redirect all test-process log files to a temp directory so pytest runs never
# write to the production logs/ directory (v3_live.log, decisions.jsonl,
# idle_scheduler.log).  The temp directory is removed when the session ends.

_test_log_dir: str | None = None


def pytest_configure(config: object) -> None:
    global _test_log_dir
    _test_log_dir = tempfile.mkdtemp(prefix="kiro-test-logs-")
    os.environ["KIRO_LOG_DIR"] = _test_log_dir


def pytest_unconfigure(config: object) -> None:
    global _test_log_dir
    if _test_log_dir:
        shutil.rmtree(_test_log_dir, ignore_errors=True)
        _test_log_dir = None
