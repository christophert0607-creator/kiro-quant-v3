"""Explicit execution state machine for Kiro Quant V3.

Three named paths prevent local position state from leaking across modes:

  simulate_only()  — auto_trade=False.  Records decision; never touches
                      position state or broker.

  paper_fill()     — paper_trading=True.  Applies a deterministic fill to
                      local paper portfolio state.  No broker call.

  live_order()     — real/sim live trading.  Places broker order FIRST;
                      mutates local position state ONLY after the broker
                      accepts the order (or confirmed placement).  Does not
                      mutate on exception.

Callers (LiveTradingLoop._execute) choose the path based on config flags
and must not bypass this module for live-mode state mutations.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger(__name__)


class ExecutionMode(Enum):
    SIMULATE = auto()
    PAPER = auto()
    LIVE = auto()


def resolve_execution_mode(auto_trade: bool, paper_trading: bool) -> ExecutionMode:
    """Return the correct ExecutionMode from config flags.

    Priority:
      1. auto_trade=False → SIMULATE (no side effects)
      2. paper_trading=True → PAPER (local fill, no broker)
      3. Otherwise → LIVE (real broker order)
    """
    if not auto_trade:
        return ExecutionMode.SIMULATE
    if paper_trading:
        return ExecutionMode.PAPER
    return ExecutionMode.LIVE


# ── Position mutation helpers ─────────────────────────────────────────────────

@dataclass
class PositionDelta:
    """Describes how a trade changes position state."""
    symbol: str
    side: str          # "BUY" or "SELL"
    qty: int
    fill_price: float
    reason: str


def _apply_long_buy(positions: dict[str, int], sym: str, qty: int) -> None:
    positions[sym] = positions.get(sym, 0) + qty


def _revert_long_buy(positions: dict[str, int], sym: str, qty: int) -> None:
    positions[sym] = max(0, positions.get(sym, 0) - qty)


def _apply_long_sell(positions: dict[str, int], sym: str, qty: int) -> int:
    """Returns remaining qty after sell."""
    new_qty = max(0, positions.get(sym, 0) - qty)
    positions[sym] = new_qty
    return new_qty


def _revert_long_sell(positions: dict[str, int], sym: str, qty: int) -> None:
    positions[sym] = positions.get(sym, 0) + qty


# ── Three explicit execution paths ────────────────────────────────────────────

def simulate_only(
    delta: PositionDelta,
    *,
    log_decision: Optional[Callable[[dict[str, Any]], None]] = None,
) -> None:
    """SIMULATE path: record decision only, no position mutation, no broker call.

    Args:
        delta: Trade intent.
        log_decision: Optional callback for structured decision logging.
    """
    record: dict[str, Any] = {
        "mode": "simulate",
        "symbol": delta.symbol,
        "side": delta.side,
        "qty": delta.qty,
        "price": delta.fill_price,
        "reason": delta.reason,
    }
    logger.debug("SIMULATE %s %s qty=%d price=%.4f reason=%s",
                 delta.symbol, delta.side, delta.qty, delta.fill_price, delta.reason)
    if log_decision:
        log_decision(record)


def paper_fill(
    delta: PositionDelta,
    positions: dict[str, int],
    *,
    log_decision: Optional[Callable[[dict[str, Any]], None]] = None,
) -> int:
    """PAPER path: apply deterministic local fill, no broker call.

    Mutates *positions* in-place.

    Returns:
        Updated position qty for ``delta.symbol`` after the fill.
    """
    sym = delta.symbol
    side = delta.side.upper()

    if side == "BUY":
        _apply_long_buy(positions, sym, delta.qty)
    elif side == "SELL":
        _apply_long_sell(positions, sym, delta.qty)

    new_qty = positions.get(sym, 0)
    record: dict[str, Any] = {
        "mode": "paper",
        "symbol": sym,
        "side": side,
        "qty": delta.qty,
        "fill_price": delta.fill_price,
        "reason": delta.reason,
        "position_after": new_qty,
    }
    logger.info("PAPER_FILL %s %s qty=%d fill=%.4f reason=%s → position=%d",
                sym, side, delta.qty, delta.fill_price, delta.reason, new_qty)
    if log_decision:
        log_decision(record)
    return new_qty


def live_order(
    delta: PositionDelta,
    positions: dict[str, int],
    place_order_fn: Callable[[str, int, str, float], Any],
    *,
    log_decision: Optional[Callable[[dict[str, Any]], None]] = None,
) -> bool:
    """LIVE path: submit broker order first, then mutate position state on success.

    This ensures local state is never ahead of broker state. If the broker
    call raises any exception, position state is not mutated and the error
    is logged.

    Args:
        delta: Trade intent.
        positions: Mutable dict of {symbol: long_qty}. Updated ONLY on success.
        place_order_fn: Callable matching
            ``(symbol, qty, side, price) -> order_result``.
            Must raise on placement failure.
        log_decision: Optional structured log callback.

    Returns:
        True if the order was accepted and state was updated; False otherwise.
    """
    sym = delta.symbol
    side = delta.side.upper()

    try:
        place_order_fn(sym, delta.qty, side, delta.fill_price)
    except Exception as exc:
        logger.error(
            "[LIVE_ORDER][%s] Broker rejected %s qty=%d: %s — position state NOT changed",
            sym, side, delta.qty, exc,
        )
        record: dict[str, Any] = {
            "mode": "live",
            "symbol": sym,
            "side": side,
            "qty": delta.qty,
            "fill_price": delta.fill_price,
            "reason": delta.reason,
            "status": "BROKER_REJECTED",
            "error": str(exc),
        }
        if log_decision:
            log_decision(record)
        return False

    # Broker accepted — now safe to mutate local state.
    if side == "BUY":
        _apply_long_buy(positions, sym, delta.qty)
    elif side == "SELL":
        _apply_long_sell(positions, sym, delta.qty)

    new_qty = positions.get(sym, 0)
    record = {
        "mode": "live",
        "symbol": sym,
        "side": side,
        "qty": delta.qty,
        "fill_price": delta.fill_price,
        "reason": delta.reason,
        "status": "ACCEPTED",
        "position_after": new_qty,
    }
    logger.info("LIVE_ORDER %s %s qty=%d fill=%.4f reason=%s → position=%d",
                sym, side, delta.qty, delta.fill_price, delta.reason, new_qty)
    if log_decision:
        log_decision(record)
    return True


def dispatch_execution(
    delta: PositionDelta,
    mode: ExecutionMode,
    positions: dict[str, int],
    place_order_fn: Optional[Callable[[str, int, str, float], Any]] = None,
    *,
    log_decision: Optional[Callable[[dict[str, Any]], None]] = None,
) -> bool:
    """Route to the correct execution path based on mode.

    Returns True if position state was mutated (paper or live success),
    False if simulated or live failure.
    """
    if mode == ExecutionMode.SIMULATE:
        simulate_only(delta, log_decision=log_decision)
        return False

    if mode == ExecutionMode.PAPER:
        paper_fill(delta, positions, log_decision=log_decision)
        return True

    # LIVE
    if place_order_fn is None:
        raise ValueError("place_order_fn is required for LIVE execution mode")
    return live_order(delta, positions, place_order_fn, log_decision=log_decision)
