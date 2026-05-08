#!/usr/bin/env python3
"""Preflight checks for Kiro Quant runtime safety."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path

from validate_config import load_json, validate, _get_futu_cfg


def _tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run_preflight(config_path: str = "config.json", strict: bool = False) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []

    cfg = load_json(Path(config_path))
    errors.extend(validate(cfg))
    if errors:
        return warnings, errors

    paper = bool(cfg.get("paper_trading", True))
    auto_trade = bool(cfg.get("auto_trade", True))
    v3_live = cfg.get("v3_live", {}) if isinstance(cfg.get("v3_live"), dict) else {}
    paper = bool(v3_live.get("paper_trading", paper))
    auto_trade = bool(v3_live.get("auto_trade", auto_trade))

    futu_cfg = _get_futu_cfg(cfg)
    futu_host = str(futu_cfg.get("host") or os.environ.get("FUTU_OPEND_HOST") or "127.0.0.1")
    futu_port = int(futu_cfg.get("port") or os.environ.get("FUTU_OPEND_PORT") or 11111)

    infoway_key = os.environ.get("INFOWAY_API_KEY", "").strip()
    allow_dev_fallback = os.environ.get("KIRO_ALLOW_DEV_FALLBACK_KEYS", "0").strip() in {"1", "true", "TRUE", "yes", "on"}

    if not infoway_key:
        msg = "INFOWAY_API_KEY not set; runtime may rely on fallback data only"
        if strict and not allow_dev_fallback and not paper:
            errors.append(msg)
        else:
            warnings.append(msg)

    if not paper:
        if not _tcp_open(futu_host, futu_port):
            errors.append(f"Futu OpenD not reachable at {futu_host}:{futu_port}")
        trade_pwd = os.environ.get("FUTU_TRADE_PASSWORD", os.environ.get("FUTU_TRADE_PWD", "")).strip()
        if auto_trade and not trade_pwd:
            errors.append("FUTU_TRADE_PASSWORD / FUTU_TRADE_PWD not set for non-paper trading")
    else:
        if not _tcp_open(futu_host, futu_port):
            warnings.append(f"Futu OpenD not reachable at {futu_host}:{futu_port} (acceptable in paper mode)")

    if auto_trade and not paper:
        warnings.append("Live auto-trading mode requested; ensure broker account, market hours, and risk limits are reviewed")

    return warnings, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Kiro Quant preflight checks")
    parser.add_argument("--config", default="config.json", help="Path to config JSON")
    parser.add_argument("--strict", action="store_true", help="Fail on missing production-facing requirements")
    args = parser.parse_args()

    warnings, errors = run_preflight(config_path=args.config, strict=args.strict)

    for msg in warnings:
        print(f"[preflight][warn] {msg}")
    for msg in errors:
        print(f"[preflight][error] {msg}")

    if errors:
        return 1
    print(json.dumps({"warnings": warnings, "status": "ok"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
