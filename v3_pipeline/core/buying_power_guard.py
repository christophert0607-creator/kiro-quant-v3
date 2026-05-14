"""Buying Power Guard — intelligent preflight for order sizing.

Prevents repeated broker rejects when account buying power is exhausted.
Caches buying-power checks per-symbol to reduce API calls and log noise.

Usage:
    guard = BuyingPowerGuard(connector)
    can_trade, reason = guard.can_execute(symbol, qty, price, side="BUY")
    if not can_trade:
        logger.warning("Skip %s: %s", symbol, reason)
        return
"""

import json
import logging
import time
from typing import Optional, Tuple


class BuyingPowerGuard:
    """Smart buying-power preflight with caching and structured events."""

    # TTL for cached "insufficient" decisions (seconds)
    INSUFFICIENT_CACHE_TTL: float = 300.0  # 5 minutes
    # TTL for cached "sufficient" decisions (seconds)
    SUFFICIENT_CACHE_TTL: float = 30.0

    def __init__(self, connector: Optional[object] = None) -> None:
        self.connector = connector
        self.logger = logging.getLogger(self.__class__.__name__)
        # symbol -> (decision: bool, expiry: float, details: dict)
        self._cache: dict[str, Tuple[bool, float, dict]] = {}
        # Track symbols that repeatedly fail to reduce noise
        self._insufficient_count: dict[str, int] = {}
        self._INSUFFICIENT_NOISE_THRESHOLD: int = 3

    def _get_cached(self, symbol: str) -> Optional[Tuple[bool, dict]]:
        """Return cached decision if still valid."""
        entry = self._cache.get(symbol)
        if entry is None:
            return None
        decision, expiry, details = entry
        if time.time() > expiry:
            self._cache.pop(symbol, None)
            return None
        return decision, details

    def _set_cached(self, symbol: str, decision: bool, details: dict) -> None:
        """Cache a buying-power decision."""
        ttl = self.SUFFICIENT_CACHE_TTL if decision else self.INSUFFICIENT_CACHE_TTL
        self._cache[symbol] = (decision, time.time() + ttl, details)

    def _fetch_buying_power(self, market: str = "US") -> dict:
        """Fetch account buying-power metrics from connector."""
        if self.connector is None:
            return {"cash": 0.0, "available_funds": 0.0, "power": 0.0, "max_buying_power": 0.0}

        # Try per-market sync first (newer API)
        if hasattr(self.connector, "get_sync_assets_for_market"):
            try:
                assets = self.connector.get_sync_assets_for_market(market)
                return {
                    "cash": float(assets.get("cash", 0.0)),
                    "available_funds": float(assets.get("available_funds", 0.0)),
                    "power": float(assets.get("power", 0.0)),
                    "max_buying_power": float(assets.get("max_buying_power", 0.0)),
                }
            except Exception as exc:
                self.logger.debug("Per-market asset sync failed: %s", exc)

        # Fall back to global sync
        if hasattr(self.connector, "get_sync_assets"):
            try:
                assets = self.connector.get_sync_assets()
                return {
                    "cash": float(assets.get("cash", 0.0)),
                    "available_funds": float(assets.get("available_funds", 0.0)),
                    "power": float(assets.get("power", 0.0)),
                    "max_buying_power": float(assets.get("max_buying_power", 0.0)),
                }
            except Exception as exc:
                self.logger.debug("Global asset sync failed: %s", exc)

        return {"cash": 0.0, "available_funds": 0.0, "power": 0.0, "max_buying_power": 0.0}

    def can_execute(
        self,
        symbol: str,
        qty: int,
        price: float,
        side: str = "BUY",
        market: str = "US",
        margin_ratio: float = 1.0,
    ) -> Tuple[bool, str]:
        """Return (can_execute, reason) for an order.

        Args:
            symbol: Stock symbol
            qty: Order quantity
            price: Order price per share
            side: "BUY" or "SELL"
            market: "US" or "HK"
            margin_ratio: Margin requirement (1.0 = no margin)

        Returns:
            (True, "ok") if order can proceed
            (False, reason) if order should be skipped
        """
        side_upper = side.upper()
        if side_upper != "BUY":
            # SELL orders don't need buying-power checks
            return True, "sell_side_no_check"

        if qty <= 0:
            return False, "qty_must_be_positive"

        # Check cache first
        cached = self._get_cached(symbol)
        if cached is not None:
            decision, details = cached
            if not decision:
                # Reduce log noise for repeated insufficient decisions
                count = self._insufficient_count.get(symbol, 0) + 1
                self._insufficient_count[symbol] = count
                if count <= self._INSUFFICIENT_NOISE_THRESHOLD:
                    event = {
                        "event": "buying_power_skip_cached",
                        "symbol": symbol,
                        "qty": qty,
                        "price": price,
                        "reason": details.get("reason", "cached_insufficient"),
                        "cache_hits": count,
                    }
                    self.logger.warning("%s", json.dumps(event))
                elif count == self._INSUFFICIENT_NOISE_THRESHOLD + 1:
                    self.logger.warning(
                        "Buying power insufficient for %s (and %d more symbols) — suppressing further logs",
                        symbol,
                        len(self._insufficient_count),
                    )
            return decision, details.get("reason", "cached")

        # Calculate required capital
        est_cost = float(max(price, 0.0)) * int(qty) * float(margin_ratio)
        if est_cost <= 0:
            return False, "est_cost_zero"

        # Fetch buying power
        bp = self._fetch_buying_power(market)
        cash = bp["cash"]
        available_funds = bp["available_funds"]
        power = bp["power"]
        max_buying_power = bp["max_buying_power"]

        # Use the most permissive available metric
        effective_bp = max(cash, available_funds, power, max_buying_power)

        if effective_bp < est_cost:
            details = {
                "symbol": symbol,
                "qty": qty,
                "price": round(price, 4),
                "est_cost": round(est_cost, 2),
                "cash": round(cash, 2),
                "available_funds": round(available_funds, 2),
                "power": round(power, 2),
                "max_buying_power": round(max_buying_power, 2),
                "effective_bp": round(effective_bp, 2),
                "reason": "effective_bp < est_cost",
            }
            self._set_cached(symbol, False, details)
            self._insufficient_count[symbol] = 1

            event = {
                "event": "buying_power_insufficient",
                **details,
            }
            self.logger.warning("%s", json.dumps(event))
            return False, details["reason"]

        # Sufficient buying power
        details = {
            "symbol": symbol,
            "qty": qty,
            "price": round(price, 4),
            "est_cost": round(est_cost, 2),
            "effective_bp": round(effective_bp, 2),
            "reason": "ok",
        }
        self._set_cached(symbol, True, details)
        # Reset insufficient counter on success
        self._insufficient_count.pop(symbol, None)
        return True, "ok"

    def invalidate_cache(self, symbol: Optional[str] = None) -> None:
        """Invalidate buying-power cache for a symbol, or all symbols."""
        if symbol is None:
            self._cache.clear()
            self._insufficient_count.clear()
            self.logger.info("BuyingPowerGuard cache cleared (all symbols)")
        else:
            self._cache.pop(symbol, None)
            self._insufficient_count.pop(symbol, None)
            self.logger.debug("BuyingPowerGuard cache cleared for %s", symbol)

    def get_stats(self) -> dict:
        """Return guard statistics for observability."""
        return {
            "cached_symbols": len(self._cache),
            "insufficient_symbols": len(self._insufficient_count),
            "insufficient_details": {
                sym: count for sym, count in self._insufficient_count.items()
            },
        }
