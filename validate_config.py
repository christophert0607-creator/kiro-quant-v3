#!/usr/bin/env python3
"""Lightweight config validation for Kiro Quant.

Purpose:
- fail fast on obvious config drift / missing keys
- provide a CI-friendly validation command
- keep validation dependency-free
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


def validate(cfg: dict) -> list[str]:
    errors: list[str] = []

    futu_cfg = cfg.get("futu_api_config")
    ensure(isinstance(futu_cfg, dict), "missing object: futu_api_config", errors)
    if isinstance(futu_cfg, dict):
        ensure(bool(futu_cfg.get("host")), "futu_api_config.host is required", errors)
        ensure(isinstance(futu_cfg.get("port"), int), "futu_api_config.port must be an integer", errors)

    market_defaults = cfg.get("market_defaults")
    ensure(isinstance(market_defaults, dict), "missing object: market_defaults", errors)

    pref = cfg.get("data_source_preference")
    ensure(isinstance(pref, list) and len(pref) > 0, "data_source_preference must be a non-empty list", errors)

    v3_live = cfg.get("v3_live")
    ensure(isinstance(v3_live, dict), "missing object: v3_live", errors)
    if not isinstance(v3_live, dict):
        return errors

    symbols = v3_live.get("symbols_list")
    ensure(isinstance(symbols, list) and len(symbols) > 0, "v3_live.symbols_list must be a non-empty list", errors)

    runtime_profile = str(v3_live.get("runtime_profile", "")).lower()
    ensure(runtime_profile in ALLOWED_RUNTIME_PROFILES, "v3_live.runtime_profile must be one of: lite, standard", errors)

    prediction_thresholds = v3_live.get("prediction_thresholds")
    ensure(isinstance(prediction_thresholds, dict), "v3_live.prediction_thresholds must be an object", errors)
    if isinstance(symbols, list) and isinstance(prediction_thresholds, dict):
        missing = [s for s in symbols if s not in prediction_thresholds]
        ensure(not missing, f"v3_live.prediction_thresholds missing symbols: {', '.join(missing)}", errors)

    for key in [
        "polling_seconds",
        "buy_cooldown_cycles",
        "max_positions",
        "max_hold_bars",
    ]:
        ensure(isinstance(v3_live.get(key), int) and v3_live.get(key) > 0, f"v3_live.{key} must be a positive integer", errors)

    for key in [
        "stop_loss_pct",
        "quick_take_profit_pct",
        "max_position_fraction_per_symbol",
        "ruin_threshold",
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
