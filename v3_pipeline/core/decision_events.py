"""DecisionEvent schema and best-effort JSONL writer for trade cycle observability.

All cycle phases write through this module so a single JSONL file can
reconstruct: quote → signal → risk → execution → state persist.

Usage::

    from v3_pipeline.core.decision_events import DecisionEvent, emit_event

    emit_event(structured_logger, DecisionEvent.signal_generated(
        symbol="TSLA", market="US", side="BUY",
        prediction=0.015, threshold=0.01,
    ))

The writer is always best-effort: if anything fails (disk full, serialisation
error, missing logger) the exception is swallowed so the live loop is never
interrupted.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


# ── Canonical event type names ────────────────────────────────────────────────

BROKER_SYNC = "broker_sync"
SIGNAL_GENERATED = "signal_generated"
RISK_BLOCKED = "risk_blocked"
RISK_ALLOWED = "risk_allowed"
ORDER_ATTEMPT = "order_attempt"
ORDER_RESULT = "order_result"
STATE_PERSISTED = "state_persisted"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DecisionEvent:
    """Factory for well-typed DecisionEvent dicts.

    Each class method returns a plain dict that can be serialised to JSONL.
    Using dicts (not a class) keeps the format stable and easy to extend.
    """

    @staticmethod
    def broker_sync(
        *,
        positions: dict[str, int],
        account_value: float,
        active_markets: list[str],
        account_routing: dict | None = None,
    ) -> dict[str, Any]:
        return {
            "event": BROKER_SYNC,
            "ts": _now_iso(),
            "positions": positions,
            "account_value": account_value,
            "active_markets": active_markets,
            "account_routing": account_routing or {},
        }

    @staticmethod
    def signal_generated(
        *,
        symbol: str,
        market: str,
        side: str,
        prediction: float,
        threshold: float,
        indicators: dict | None = None,
    ) -> dict[str, Any]:
        return {
            "event": SIGNAL_GENERATED,
            "ts": _now_iso(),
            "symbol": symbol,
            "market": market,
            "side": side,
            "prediction": round(float(prediction), 6),
            "threshold": round(float(threshold), 6),
            "indicators": indicators or {},
        }

    @staticmethod
    def risk_blocked(
        *,
        symbol: str,
        market: str,
        side: str,
        gate_name: str,
        reason: str,
        metrics: dict | None = None,
    ) -> dict[str, Any]:
        return {
            "event": RISK_BLOCKED,
            "ts": _now_iso(),
            "symbol": symbol,
            "market": market,
            "side": side,
            "gate_name": gate_name,
            "reason": reason,
            "metrics": metrics or {},
        }

    @staticmethod
    def risk_allowed(
        *,
        symbol: str,
        market: str,
        side: str,
        gate_name: str,
        metrics: dict | None = None,
    ) -> dict[str, Any]:
        return {
            "event": RISK_ALLOWED,
            "ts": _now_iso(),
            "symbol": symbol,
            "market": market,
            "side": side,
            "gate_name": gate_name,
            "metrics": metrics or {},
        }

    @staticmethod
    def order_attempt(
        *,
        symbol: str,
        market: str,
        side: str,
        qty: int,
        price: float,
        reason: str,
        account_id: int | None = None,
        paper: bool = False,
    ) -> dict[str, Any]:
        return {
            "event": ORDER_ATTEMPT,
            "ts": _now_iso(),
            "symbol": symbol,
            "market": market,
            "side": side,
            "qty": qty,
            "price": round(float(price), 4),
            "reason": reason,
            "account_id": account_id,
            "paper": paper,
        }

    @staticmethod
    def order_result(
        *,
        symbol: str,
        market: str,
        side: str,
        qty: int,
        fill_price: float,
        status: str,
        reason: str = "",
        error: str | None = None,
    ) -> dict[str, Any]:
        return {
            "event": ORDER_RESULT,
            "ts": _now_iso(),
            "symbol": symbol,
            "market": market,
            "side": side,
            "qty": qty,
            "fill_price": round(float(fill_price), 4),
            "status": status,
            "reason": reason,
            "error": error,
        }

    @staticmethod
    def state_persisted(
        *,
        symbol: str,
        positions_snapshot: dict,
    ) -> dict[str, Any]:
        return {
            "event": STATE_PERSISTED,
            "ts": _now_iso(),
            "symbol": symbol,
            "positions_snapshot": positions_snapshot,
        }


def emit_event(
    logger: logging.Logger | None,
    event: dict[str, Any],
) -> None:
    """Write one DecisionEvent dict as a JSONL line (best-effort, never raises).

    Args:
        logger: A Logger whose handlers write to decisions.jsonl.
                If None or if writing fails, the event is silently dropped.
        event:  A dict produced by DecisionEvent.*() factory methods.
    """
    try:
        if logger is not None:
            logger.info(json.dumps(event, ensure_ascii=False))
    except Exception:
        pass
