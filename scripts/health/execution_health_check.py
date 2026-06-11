#!/usr/bin/env python3
"""Execution health check for V3 broker/order liveness.

Detects broker-side zombie orders and locked positions.  Account and OpenD
settings are read from config.json/env so this script is safe across profiles
and SIM account rotations.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from futu import OpenSecTradeContext, RET_OK, SecurityFirm, TrdEnv, TrdMarket


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("KIRO_CONFIG_PATH", ROOT / "config.json"))
ZOMBIE_AFTER = timedelta(hours=float(os.getenv("KIRO_ZOMBIE_ORDER_HOURS", "1")))


def _load_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_settings() -> tuple[str, int, int]:
    cfg = _load_config()
    futu = cfg.get("futu", {}) if isinstance(cfg, dict) else {}
    accounts = futu.get("accounts", {}) if isinstance(futu, dict) else {}
    us_acc = accounts.get("US", {}) if isinstance(accounts, dict) else {}

    host = os.getenv("FUTU_OPEND_HOST") or str(futu.get("host") or "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT") or futu.get("port") or 11112)
    acc_id = int(
        os.getenv("FUTU_US_ACC_ID")
        or os.getenv("FUTU_TARGET_ACC_ID")
        or us_acc.get("target_acc_id")
        or futu.get("target_acc_id")
        or 0
    )
    if acc_id <= 0:
        raise RuntimeError("missing_us_account_id: set futu.accounts.US.target_acc_id or FUTU_US_ACC_ID")
    return host, port, acc_id


def check_execution_health() -> dict[str, Any]:
    host, port, acc_id = _resolve_settings()
    ctx = OpenSecTradeContext(
        filter_trdmarket=TrdMarket.NONE,
        host=host,
        port=port,
        security_firm=SecurityFirm.FUTUSECURITIES,
    )
    health: dict[str, Any] = {
        "status": "HEALTHY",
        "issues": [],
        "metrics": {
            "account_id": acc_id,
            "pending_orders": 0,
            "zombie_orders": 0,
            "locked_positions": 0,
        },
    }
    try:
        ret_ord, orders = ctx.order_list_query(trd_env=TrdEnv.SIMULATE, acc_id=acc_id)
        if ret_ord != RET_OK:
            health["status"] = "ERROR"
            health["issues"].append(f"order_list_query_failed:{ret_ord}")
        else:
            health["metrics"]["pending_orders"] = int(len(orders))
            now = datetime.now()
            zombies = 0
            for _, row in orders.iterrows():
                if str(row.get("order_status")) != "SUBMITTED":
                    continue
                try:
                    create_time = datetime.strptime(str(row.get("create_time")), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                if now - create_time > ZOMBIE_AFTER:
                    zombies += 1
            health["metrics"]["zombie_orders"] = zombies
            if zombies > 0:
                health["status"] = "UNHEALTHY"
                health["issues"].append(f"Found {zombies} zombie orders (SUBMITTED > {ZOMBIE_AFTER}).")

        ret_pos, pos = ctx.position_list_query(trd_env=TrdEnv.SIMULATE, acc_id=acc_id)
        if ret_pos != RET_OK:
            health["status"] = "ERROR"
            health["issues"].append(f"position_list_query_failed:{ret_pos}")
        else:
            locked = 0
            for _, row in pos.iterrows():
                try:
                    qty = float(row.get("qty") or 0)
                    can_sell_qty = float(row.get("can_sell_qty") or 0)
                except Exception:
                    continue
                if qty > 0 and can_sell_qty == 0:
                    locked += 1
            health["metrics"]["locked_positions"] = locked
            if locked > 0:
                health["status"] = "UNHEALTHY"
                health["issues"].append(f"Found {locked} locked positions (qty > 0 but can_sell_qty == 0).")
    except Exception as exc:
        health["status"] = "ERROR"
        health["issues"].append(f"Health check exception: {exc}")
    finally:
        ctx.close()
    return health


if __name__ == "__main__":
    print(json.dumps(check_execution_health(), indent=2, ensure_ascii=False))
