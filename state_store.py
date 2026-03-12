#!/usr/bin/env python3
"""
Quant Engine v2 - State Store
=====================================
✅ 持久化交易狀態到 JSON
✅ 防止重複下單
✅ 追蹤今日 PnL
✅ 熔斷狀態管理
"""

import json
import time
import logging
import hashlib
from datetime import datetime, date
from config import STATE_PATH

log = logging.getLogger("StateStore")


def _default_state() -> dict:
    return {
        "date": str(date.today()),
        "daily_pnl": 0.0,
        "daily_trades": 0,
        "consecutive_errors": 0,
        "circuit_broken": False,
        "circuit_break_time": None,
        "last_orders": {},       # code -> {"hash": str, "time": float}
        "positions": {},         # code -> qty
        "total_invested": 0.0,
    }


def _sanitize_last_order_timestamps(state: dict):
    """修正異常時間戳，避免未來時間導致冷卻/去重邏輯失真。"""
    now = time.time()
    last_orders = state.get("last_orders", {})
    if not isinstance(last_orders, dict):
        return

    changed = False
    for item in last_orders.values():
        if not isinstance(item, dict):
            continue
        ts = item.get("time")
        if not isinstance(ts, (int, float)):
            continue
        # 超過現在 5 分鐘視為異常未來時間
        if ts > now + 300:
            item["time"] = now
            changed = True

    if changed:
        log.warning("⚠️ 偵測到未來時間戳，已自動修正 last_orders.time")


def load() -> dict:
    try:
        with open(STATE_PATH, "r") as f:
            loaded = json.load(f)

        # 與預設鍵合併，避免舊版 state 缺鍵導致邏輯分歧
        s = _default_state()
        if isinstance(loaded, dict):
            s.update(loaded)

        _sanitize_last_order_timestamps(s)

        # 如果日期不同（新的一天）→ 重置每日狀態
        if s.get("date") != str(date.today()):
            log.info("📅 新的一天，重置每日狀態")
            s.update({
                "date": str(date.today()),
                "daily_pnl": 0.0,
                "daily_trades": 0,
                "consecutive_errors": 0,
                "circuit_broken": False,
                "circuit_break_time": None,
            })
            save(s)
        return s
    except (FileNotFoundError, json.JSONDecodeError):
        s = _default_state()
        save(s)
        return s


def save(state: dict):
    """原子寫入，避免中途寫檔導致 state.json 損毀"""
    tmp_path = f"{STATE_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    import os
    os.replace(tmp_path, STATE_PATH)


def order_hash(code: str, signal: str, qty: int, price: float) -> str:
    raw = f"{code}|{signal}|{qty}|{price:.2f}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def is_duplicate_order(state: dict, code: str, signal: str, qty: int, price: float, cooldown_secs: int) -> bool:
    """檢查是否重複下單（同一股票 cooldown 期內）"""
    last = state.get("last_orders", {}).get(code)
    if not last:
        return False
    elapsed = time.time() - last.get("time", 0)
    same_hash = last.get("hash") == order_hash(code, signal, qty, price)
    if elapsed < cooldown_secs and same_hash:
        log.warning(f"🔁 重複下單保護: {code} 在 {elapsed:.0f}s 前剛下單相同指令")
        return True
    return False


def record_order(state: dict, code: str, signal: str, qty: int, price: float):
    if "last_orders" not in state:
        state["last_orders"] = {}
    state["last_orders"][code] = {
        "hash": order_hash(code, signal, qty, price),
        "time": time.time(),
        "signal": signal,
        "qty": qty,
        "price": price,
    }
    state["daily_trades"] = state.get("daily_trades", 0) + 1


def record_error(state: dict) -> int:
    state["consecutive_errors"] = state.get("consecutive_errors", 0) + 1
    return state["consecutive_errors"]


def reset_errors(state: dict):
    state["consecutive_errors"] = 0


def set_circuit_break(state: dict, broken: bool):
    state["circuit_broken"] = broken
    state["circuit_break_time"] = time.time() if broken else None
    if broken:
        log.critical("🔴 CIRCUIT BREAKER TRIGGERED — 停止所有交易")
    else:
        log.info("🟢 Circuit breaker 已重置")


def is_circuit_broken(state: dict) -> bool:
    return state.get("circuit_broken", False)


def update_daily_pnl(state: dict, pnl_delta: float):
    state["daily_pnl"] = state.get("daily_pnl", 0.0) + pnl_delta


def get_daily_pnl(state: dict) -> float:
    return state.get("daily_pnl", 0.0)
