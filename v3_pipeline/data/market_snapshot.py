"""Immutable quote snapshot for one trading cycle.

At the start of each cycle, a MarketDataSnapshot is built once from the
QuoteCache. Symbol cycles then read from the snapshot instead of hitting
the provider directly, guaranteeing:

- Each symbol is fetched at most once per cycle (no duplicate provider calls).
- Partial failures are isolated per symbol — a miss for one symbol does not
  skip others.
- The snapshot is frozen: new provider responses during the cycle do not
  change what the cycle sees.

Usage::

    snapshot = MarketDataSnapshot.from_cache(cache, symbols)
    quote = snapshot.get("TSLA")    # returns dict or None
    miss  = snapshot.miss_reason("TSLA")  # "stale_cache", "no_data", or None
"""
from __future__ import annotations

from typing import Any


_MISS_STALE_CACHE = "stale_cache"
_MISS_NO_DATA = "no_data"


class MarketDataSnapshot:
    """Immutable snapshot of quotes for a set of symbols.

    Built once at cycle start; read-only thereafter so the symbol-cycle
    code never mutates shared cache state.
    """

    def __init__(
        self,
        quotes: dict[str, dict],
        misses: dict[str, str],
    ) -> None:
        self._quotes = quotes
        self._misses = misses

    # ── Accessors ─────────────────────────────────────────────────────────────

    def get(self, symbol: str, default: Any = None) -> Any:
        """Return the quote dict for *symbol*, or *default* if absent / missed."""
        return self._quotes.get(symbol, default)

    def miss_reason(self, symbol: str) -> str | None:
        """Return the miss reason string for *symbol*, or None if the quote is present."""
        return self._misses.get(symbol)

    def present_symbols(self) -> list[str]:
        """Symbols for which quotes are available."""
        return list(self._quotes)

    def missing_symbols(self) -> list[str]:
        """Symbols for which quotes are absent."""
        return list(self._misses)

    def __len__(self) -> int:
        return len(self._quotes)

    def __contains__(self, symbol: object) -> bool:
        return symbol in self._quotes

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_cache(
        cls,
        cache: Any,
        symbols: list[str],
    ) -> "MarketDataSnapshot":
        """Build a snapshot by reading each symbol from *cache*.

        Args:
            cache: Any object with a ``get(symbol, default)`` method
                   (QuoteCache-compatible).
            symbols: The full list of symbols for this cycle.

        Returns:
            A MarketDataSnapshot with quotes for symbols that had a fresh
            cache entry, and miss reasons for the rest.
        """
        quotes: dict[str, dict] = {}
        misses: dict[str, str] = {}

        for sym in symbols:
            quote = cache.get(sym) if cache is not None else None
            if quote is not None and isinstance(quote, dict) and quote.get("Close", 0) > 0:
                quotes[sym] = quote
            else:
                misses[sym] = _MISS_NO_DATA if quote is None else _MISS_STALE_CACHE

        return cls(quotes=quotes, misses=misses)
