from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class HealthState:
    status: str = "READY"  # READY / DEGRADED / BLOCKED
    mode: str = "UNKNOWN"
    market: str = "UNKNOWN"
    heartbeat_ok: bool = True
    broker_sync_ok: bool = True
    positions_loaded: bool = False
    trade_enabled: bool = True
    consecutive_errors: int = 0
    last_error_code: str | None = None
    last_error_message: str | None = None
    last_signal: str | None = None
    last_trade: dict[str, Any] | None = None
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)


class HealthMonitor:
    def __init__(self, path: str | Path = "health.json") -> None:
        self.path = Path(path)
        self.state = HealthState()
        self.events: list[dict[str, Any]] = []

    def set_mode(self, mode: str) -> None:
        self.state.mode = str(mode or "UNKNOWN")

    def set_market(self, market: str) -> None:
        self.state.market = str(market or "UNKNOWN")

    def set_status(self, status: str) -> None:
        normalized = str(status or "READY").upper()
        if normalized not in {"READY", "DEGRADED", "BLOCKED"}:
            normalized = "READY"
        current_rank = self._status_rank(self.state.status)
        next_rank = self._status_rank(normalized)
        self.state.status = normalized if next_rank >= current_rank else self.state.status
        self.state.trade_enabled = self.state.status != "BLOCKED"

    def reset_status(self, status: str = "READY") -> None:
        normalized = str(status or "READY").upper()
        if normalized not in {"READY", "DEGRADED", "BLOCKED"}:
            normalized = "READY"
        self.state.status = normalized
        self.state.trade_enabled = normalized != "BLOCKED"

    def set_heartbeat(self, ok: bool) -> None:
        self.state.heartbeat_ok = bool(ok)

    def set_broker_sync(self, ok: bool) -> None:
        self.state.broker_sync_ok = bool(ok)

    def set_positions(self, positions: dict[str, dict[str, Any]]) -> None:
        normalized: dict[str, dict[str, Any]] = {}
        for symbol, payload in (positions or {}).items():
            if not isinstance(payload, dict):
                continue
            qty = int(float(payload.get("qty", 0) or 0))
            avg_cost = float(payload.get("avg_cost", payload.get("entry_price", 0.0)) or 0.0)
            normalized[str(symbol)] = {
                "qty": max(0, qty),
                "avg_cost": max(0.0, avg_cost),
                "source": payload.get("source"),
                "updated_at": payload.get("updated_at"),
            }
        self.state.positions = normalized
        self.state.positions_loaded = any(item.get("qty", 0) > 0 for item in normalized.values()) or bool(normalized)

    def set_signal(self, signal: str) -> None:
        self.state.last_signal = str(signal)

    def set_trade(self, trade: dict[str, Any]) -> None:
        self.state.last_trade = trade

    def record_event(self, code: str, message: str, severity: str = "INFO", **meta: Any) -> None:
        severity_norm = str(severity or "INFO").upper()
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "code": str(code),
            "severity": severity_norm,
            "message": str(message),
            "meta": meta,
        }
        self.events.append(event)
        self.events = self.events[-50:]

        if severity_norm in {"ERROR", "CRITICAL"}:
            self.state.consecutive_errors += 1
            self.state.last_error_code = str(code)
            self.state.last_error_message = str(message)
        elif severity_norm == "INFO":
            self.state.consecutive_errors = max(0, self.state.consecutive_errors - 1)

        if severity_norm == "CRITICAL":
            self.set_status("BLOCKED")
        elif severity_norm == "ERROR":
            self.set_status("DEGRADED")

    def should_block_trading(self) -> bool:
        if self.state.status == "BLOCKED":
            return True
        if self.state.mode == "FUTU" and not self.state.broker_sync_ok:
            return True
        if self.state.consecutive_errors >= 3:
            return True
        return False

    def write(self) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": asdict(self.state),
            "recent_events": self.events[-20:],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    @staticmethod
    def _status_rank(status: str) -> int:
        return {"READY": 0, "DEGRADED": 1, "BLOCKED": 2}.get(str(status).upper(), 0)
