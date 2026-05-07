"""Per-market runtime context for HK/US account isolation.

Each active market (HK, US) gets its own MarketContext that owns:
  - The market code string
  - Account ID for that market
  - Symbols belonging to that market
  - Position and short-position qty state for that market

Using per-market contexts prevents the shared global ``market_prefix``
from mixing HK and US account state when both markets are configured.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

MARKET_HK = "HK"
MARKET_US = "US"

_HK_SUFFIX = ".HK"


def infer_market(symbol: str) -> str:
    """Infer market from symbol suffix.

    Rules:
      - Symbols ending in ``.HK`` → ``"HK"``
      - Everything else → ``"US"``
    """
    if str(symbol).upper().endswith(_HK_SUFFIX):
        return MARKET_HK
    return MARKET_US


def broker_code(symbol: str, market: Optional[str] = None) -> str:
    """Return Futu broker code for a symbol.

    Examples:
        broker_code("0700.HK") -> "HK.0700.HK"
        broker_code("TSLA")    -> "US.TSLA"
        broker_code("TSLA", "US") -> "US.TSLA"
    """
    mkt = market if market is not None else infer_market(symbol)
    return f"{mkt}.{symbol}"


@dataclass
class MarketContext:
    """Runtime state scoped to a single market (HK or US).

    Callers should not share position state across markets; each
    MarketContext is the authoritative source for its symbols.
    """

    market: str
    account_id: Optional[int] = None
    symbols: list[str] = field(default_factory=list)
    position_qty: dict[str, int] = field(default_factory=dict)
    short_position_qty: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.market = str(self.market).upper()
        if self.market not in (MARKET_HK, MARKET_US):
            raise ValueError(f"Unsupported market: {self.market!r}. Must be 'HK' or 'US'.")

    def add_symbol(self, symbol: str) -> None:
        """Register a symbol with this context (idempotent)."""
        if symbol not in self.symbols:
            self.symbols.append(symbol)
        self.position_qty.setdefault(symbol, 0)
        self.short_position_qty.setdefault(symbol, 0)

    def broker_code_for(self, symbol: str) -> str:
        """Return Futu broker code using this context's market prefix."""
        return f"{self.market}.{symbol}"

    def long_qty(self, symbol: str) -> int:
        return int(self.position_qty.get(symbol, 0))

    def short_qty(self, symbol: str) -> int:
        return int(self.short_position_qty.get(symbol, 0))

    def active_long_symbols(self) -> list[str]:
        """Symbols with non-zero long positions."""
        return [s for s, q in self.position_qty.items() if q > 0]

    def active_short_symbols(self) -> list[str]:
        """Symbols with non-zero short positions."""
        return [s for s, q in self.short_position_qty.items() if q > 0]

    def total_active_positions(self) -> int:
        return sum(1 for q in self.position_qty.values() if q > 0) + sum(
            1 for q in self.short_position_qty.values() if q > 0
        )


def build_market_contexts(
    symbols: list[str],
    account_ids: Optional[dict[str, Optional[int]]] = None,
) -> dict[str, MarketContext]:
    """Partition a mixed symbol list into per-market MarketContexts.

    Args:
        symbols: Mixed list, e.g. ``["0700.HK", "TSLA", "9988.HK", "NVDA"]``
        account_ids: Optional ``{"HK": 1234, "US": 5678}`` for per-market
            account routing. Missing markets default to ``None``.

    Returns:
        Dict mapping market code to its MarketContext, e.g.
        ``{"HK": MarketContext(market="HK", ...), "US": MarketContext(...)}``
    """
    ids = account_ids or {}
    contexts: dict[str, MarketContext] = {}
    for sym in symbols:
        mkt = infer_market(sym)
        if mkt not in contexts:
            contexts[mkt] = MarketContext(market=mkt, account_id=ids.get(mkt))
        contexts[mkt].add_symbol(sym)
    return contexts


def sync_positions_to_contexts(
    contexts: dict[str, MarketContext],
    broker_positions: list[dict],
) -> None:
    """Update position_qty / short_position_qty from a broker position list.

    Args:
        contexts: Output of ``build_market_contexts()``.
        broker_positions: List of dicts, each with keys:
            ``code``  — Futu broker code, e.g. "HK.0700.HK" or "US.TSLA"
            ``qty``   — net quantity (positive = long, negative = short)
    """
    for row in broker_positions:
        code = str(row.get("code", ""))
        qty = int(row.get("qty", 0))
        parts = code.split(".", 1)
        if len(parts) != 2:
            continue
        mkt, sym = parts[0], parts[1]
        ctx = contexts.get(mkt)
        if ctx is None:
            continue
        if sym in ctx.symbols:
            if qty >= 0:
                ctx.position_qty[sym] = qty
                ctx.short_position_qty[sym] = 0
            else:
                ctx.position_qty[sym] = 0
                ctx.short_position_qty[sym] = abs(qty)
