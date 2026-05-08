#!/usr/bin/env python3
"""Lightweight config validation for Kiro Quant.

Purpose:
- fail fast on obvious config drift / missing keys
- provide a CI-friendly validation command
- keep validation dependency-free

Supports both legacy key structures:
  - futu_api_config.{host,port}  (PR #58 convention)
  - futu.{host,port}             (current main convention)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ALLOWED_RUNTIME_PROFILES = {"lite", "standard"}


def _err(msg: str) -> str:
    return f"[config] {msg}"


def load_json(path: Path) -> dict:
    if not path.exists():
        raise ValueError(_err(f"missing file: {path}"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(_err(f"invalid JSON in {path}: {exc}")) from exc
    if not isinstance(data, dict):
        raise ValueError(_err(f"top-level JSON object required: {path}"))
    return data


def ensure(condition: bool, msg: str, errors: list[str]) -> None:
    if not condition:
        errors.append(_err(msg))


def _get_futu_cfg(cfg: dict) -> dict:
    """Return Futu config block, supporting both futu_api_config and futu keys."""
    for key in ("futu_api_config", "futu"):
        val = cfg.get(key)
        if isinstance(val, dict):
            return val
    return {}


def validate(cfg: dict) -> list[str]:
    errors: list[str] = []

    futu_cfg = _get_futu_cfg(cfg)
    ensure(bool(futu_cfg), "missing object: futu_api_config (or futu)", errors)
    if futu_cfg:
        ensure(bool(futu_cfg.get("host")), "futu config: host is required", errors)
        ensure(isinstance(futu_cfg.get("port"), int), "futu config: port must be an integer", errors)

    # market_defaults and data_source_preference are optional in the current config structure
    # but encouraged for new deployments

    v3_live = cfg.get("v3_live")
    ensure(isinstance(v3_live, dict), "missing object: v3_live", errors)
    if not isinstance(v3_live, dict):
        return errors

    symbols = v3_live.get("symbols_list")
    # symbols_list may be empty when using dynamic watchlist — skip non-empty check

    runtime_profile = str(v3_live.get("runtime_profile", "standard")).lower()
    ensure(runtime_profile in ALLOWED_RUNTIME_PROFILES, "v3_live.runtime_profile must be one of: lite, standard", errors)

    for key in [
        "polling_seconds",
        "buy_cooldown_cycles",
        "max_positions",
        "max_hold_bars",
    ]:
        val = v3_live.get(key)
        ensure(isinstance(val, int) and val > 0, f"v3_live.{key} must be a positive integer", errors)

    for key in [
        "stop_loss_pct",
        "quick_take_profit_pct",
    ]:
        val = v3_live.get(key)
        ensure(isinstance(val, (int, float)), f"v3_live.{key} must be numeric", errors)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Kiro Quant JSON config")
    parser.add_argument("--config", default="config.json", help="Path to config JSON")
    args = parser.parse_args()

    path = Path(args.config)
    try:
        cfg = load_json(path)
        errors = validate(cfg)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    print(f"[config] OK: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
