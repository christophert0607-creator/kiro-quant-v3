#!/usr/bin/env python3
"""Read-only V3 exit-position alignment smoke check.

Compares current Futu SIM broker positions with the latest engine broker_sync
entry in logs/decisions.jsonl.  This script does not place/cancel orders and
never writes DB/config/model files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _to_engine_symbol(code: str) -> str:
    code = str(code).strip().upper()
    if code.startswith("US."):
        return code.split(".", 1)[1]
    if code.startswith("HK."):
        raw = code.split(".", 1)[1]
        return f"{raw[-4:]}.HK"
    return code


def fetch_broker_positions() -> dict[str, int]:
    import futu as ft

    ctx = ft.OpenSecTradeContext(
        host="127.0.0.1",
        port=11112,
        filter_trdmarket=ft.TrdMarket.NONE,
    )
    positions: dict[str, int] = {}
    try:
        for acc_id in (14239754, 18526451):
            ret, pos = ctx.position_list_query(trd_env=ft.TrdEnv.SIMULATE, acc_id=acc_id)
            if ret != ft.RET_OK or pos is None or pos.empty:
                continue
            for _, row in pos.iterrows():
                try:
                    qty = int(float(row.get("qty", 0) or 0))
                except Exception:
                    qty = 0
                if qty <= 0:
                    continue
                code = str(row.get("code", ""))
                symbol = _to_engine_symbol(code)
                positions[symbol] = positions.get(symbol, 0) + qty
    finally:
        ctx.close()
    return dict(sorted(positions.items()))


def latest_engine_sync(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    latest: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            item = json.loads(line)
        except Exception:
            continue
        if item.get("event") == "broker_sync":
            latest = item
    return latest


def main() -> int:
    broker = fetch_broker_positions()
    sync = latest_engine_sync(Path("logs/decisions.jsonl"))
    engine = {str(k): int(v) for k, v in (sync.get("positions") or {}).items() if int(v or 0) > 0}
    mismatches: dict[str, dict[str, int]] = {}
    for symbol in sorted(set(broker) | set(engine)):
        b = int(broker.get(symbol, 0))
        e = int(engine.get(symbol, 0))
        if b != e:
            mismatches[symbol] = {"broker": b, "engine": e}
    result = {
        "status": "ok" if not mismatches else "mismatch",
        "broker_positions": broker,
        "latest_engine_sync_ts": sync.get("ts"),
        "latest_engine_positions": engine,
        "mismatches": mismatches,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not mismatches else 2


if __name__ == "__main__":
    raise SystemExit(main())
