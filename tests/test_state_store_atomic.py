"""Acceptance tests for state_store single-entrypoint hardening.

Covers:
- Corrupted JSON falls back to default state without crashing
- Missing keys are filled from defaults
- short_positions and account_routing survive a round-trip
- save() uses tmp + os.replace (atomic write)
- main_loop._execute() does not open state.json directly
"""

import json
import os
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import state_store as ss


# ── load() resilience ─────────────────────────────────────────────────────────


def test_load_corrupted_json_returns_default(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text("{invalid json{{", encoding="utf-8")
    monkeypatch.setattr(ss, "STATE_PATH", str(state_file))

    result = ss.load()
    assert result["positions"] == {}
    assert result["circuit_broken"] is False
    assert "daily_pnl" in result


def test_load_missing_file_returns_default(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "STATE_PATH", str(tmp_path / "nonexistent.json"))
    result = ss.load()
    assert result["positions"] == {}
    assert result["daily_trades"] == 0


def test_load_missing_keys_filled_from_defaults(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"date": str(date.today()), "daily_pnl": 99.0}), encoding="utf-8")
    monkeypatch.setattr(ss, "STATE_PATH", str(state_file))

    result = ss.load()
    assert result["daily_pnl"] == 99.0
    assert "positions" in result
    assert "last_orders" in result


def test_load_preserves_short_positions(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    payload = {
        "date": str(date.today()),
        "daily_pnl": 0.0,
        "daily_trades": 0,
        "consecutive_errors": 0,
        "circuit_broken": False,
        "circuit_break_time": None,
        "last_orders": {},
        "positions": {"US.TSLA": 10},
        "short_positions": {"US.NVDA": 5},
        "total_invested": 0.0,
    }
    state_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(ss, "STATE_PATH", str(state_file))

    result = ss.load()
    assert result["short_positions"]["US.NVDA"] == 5
    assert result["positions"]["US.TSLA"] == 10


def test_load_preserves_account_routing(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    routing = {"account_routing": {"HK": {"resolved_acc_id": 14239754, "source": "config"}}}
    payload = {
        "date": str(date.today()),
        "daily_pnl": 0.0,
        "daily_trades": 0,
        "consecutive_errors": 0,
        "circuit_broken": False,
        "circuit_break_time": None,
        "last_orders": {},
        "positions": {},
        "total_invested": 0.0,
        **routing,
    }
    state_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(ss, "STATE_PATH", str(state_file))

    result = ss.load()
    assert result["account_routing"]["HK"]["resolved_acc_id"] == 14239754


# ── save() atomic write ───────────────────────────────────────────────────────


def test_save_uses_atomic_replace(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(ss, "STATE_PATH", str(state_file))

    replaced_calls = []
    original_replace = os.replace

    def spy_replace(src, dst):
        replaced_calls.append((src, dst))
        return original_replace(src, dst)

    with patch("os.replace", side_effect=spy_replace):
        ss.save({"positions": {}, "daily_pnl": 0.0})

    assert len(replaced_calls) == 1
    src, dst = replaced_calls[0]
    assert src.endswith(".tmp")
    assert dst == str(state_file)


def test_save_roundtrip_short_positions(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(ss, "STATE_PATH", str(state_file))

    state = ss.load()
    state["short_positions"] = {"0005.HK": 200}
    state["account_routing"] = {"HK": {"resolved_acc_id": 14239754}}
    ss.save(state)

    reloaded = ss.load()
    assert reloaded["short_positions"]["0005.HK"] == 200
    assert reloaded["account_routing"]["HK"]["resolved_acc_id"] == 14239754


def test_save_no_tmp_file_left_behind(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(ss, "STATE_PATH", str(state_file))

    ss.save({"positions": {}, "daily_pnl": 0.0})

    tmp = tmp_path / "state.json.tmp"
    assert not tmp.exists(), "tmp file must be cleaned up after atomic replace"


# ── main_loop.py does not bypass state_store ─────────────────────────────────


def test_main_loop_execute_no_direct_state_json_open():
    """_execute() must not call open() with 'state.json' directly."""
    src = Path("v3_pipeline/core/main_loop.py").read_text(encoding="utf-8")
    # Allow only state_store usage; reject direct open("...state.json...") or open(state_file
    import re
    # Look for open( calls that reference state_file or state.json (not in comments)
    lines = src.splitlines()
    violations = []
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "open(" in stripped and ("state.json" in stripped or "state_file" in stripped):
            violations.append(f"line {lineno}: {stripped}")
    assert violations == [], (
        "main_loop.py must use state_store, not open() state.json directly:\n"
        + "\n".join(violations)
    )
