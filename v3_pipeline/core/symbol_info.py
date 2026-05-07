"""
SymbolInfo — canonical symbol normalization utility for Kiro Quant V3.

Single tested utility that maps between:
  - canonical symbol  (e.g. "0700.HK", "TSLA")
  - broker code       (e.g. "HK.0700.HK", "US.TSLA")
  - market            (e.g. "HK", "US")
  - provider symbol   (yfinance, efinance, Futu broker code)

Replaces ad-hoc string replacements in the NO_FUTU fallback path and any caller
that manually constructs broker codes or normalizes position symbols.

Usage:
    from v3_pipeline.core.symbol_info import resolve_symbol_info, SymbolInfo

    info = resolve_symbol_info("0700.HK")
    # SymbolInfo(canonical="0700.HK", market="HK", broker_code="HK.0700.HK",
    #            yf_symbol="0700.HK", ef_symbol="00700", futu_code="HK.0700.HK")

    info2 = resolve_symbol_info("TSLA")
    # SymbolInfo(canonical="TSLA", market="US", broker_code="US.TSLA",
    #            yf_symbol="TSLA", ef_symbol="TSLA", futu_code="US.TSLA")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_HK_SUFFIX = ".HK"
_US_SUFFIX = ".US"

MARKET_HK = "HK"
MARKET_US = "US"

KNOWN_HK_PREFIXES = ("HK.",)
KNOWN_US_PREFIXES = ("US.",)


@dataclass(frozen=True)
class SymbolInfo:
    """Canonical representation of a tradable symbol across providers.

    Attributes:
        canonical:   Clean symbol without any market prefix (e.g. "0700.HK", "TSLA").
        market:      "HK" or "US".
        broker_code: Futu broker code (e.g. "HK.0700.HK", "US.TSLA").
        yf_symbol:   Symbol used with yfinance (same as canonical for most cases).
        ef_symbol:   Symbol used with efinance (strip leading zeros for HK stocks).
        futu_code:   Alias for broker_code; kept explicit for readability.
    """

    canonical: str
    market: str
    broker_code: str
    yf_symbol: str
    ef_symbol: str
    futu_code: str

    def is_hk(self) -> bool:
        return self.market == MARKET_HK

    def is_us(self) -> bool:
        return self.market == MARKET_US


def _strip_market_prefix(symbol: str) -> str:
    """Remove 'HK.' or 'US.' prefix if present.

    Examples:
        "HK.0700.HK" -> "0700.HK"
        "US.TSLA"    -> "TSLA"
        "TSLA"       -> "TSLA"
    """
    upper = symbol.upper()
    for prefix in KNOWN_HK_PREFIXES + KNOWN_US_PREFIXES:
        if upper.startswith(prefix):
            return symbol[len(prefix):]
    return symbol


def _infer_market(symbol: str) -> str:
    """Infer market from the canonical symbol (after prefix stripping).

    Rules:
        - Ends with ".HK" (case-insensitive) → HK
        - Ends with ".US" (case-insensitive) → US (strip the suffix for canonical)
        - Otherwise → US
    """
    upper = symbol.upper()
    if upper.endswith(_HK_SUFFIX):
        return MARKET_HK
    return MARKET_US


def _ef_symbol_for(canonical: str, market: str) -> str:
    """Derive the efinance symbol from a canonical symbol.

    For HK stocks efinance uses a 5-digit code without the '.HK' suffix, e.g.:
        "0700.HK" -> "00700"
        "9988.HK" -> "09988"
    For US stocks, the canonical symbol is used directly.
    """
    if market == MARKET_HK:
        base = canonical.upper().removesuffix(_HK_SUFFIX)
        try:
            return str(int(base)).zfill(5)
        except ValueError:
            return base
    return canonical


def resolve_symbol_info(
    symbol: str,
    market_override: Optional[str] = None,
) -> SymbolInfo:
    """Return a SymbolInfo for the given symbol string.

    Args:
        symbol:          Any of: canonical ("0700.HK"), broker code ("HK.0700.HK"),
                         yfinance format ("0700.HK"), or bare US symbol ("TSLA").
        market_override: If given ("HK" or "US"), override automatic market inference.

    Returns:
        A frozen SymbolInfo instance.
    """
    canonical = _strip_market_prefix(symbol.strip())
    market = market_override.upper() if market_override else _infer_market(canonical)
    if market not in (MARKET_HK, MARKET_US):
        raise ValueError(f"Unsupported market: {market!r}. Must be 'HK' or 'US'.")

    b_code = f"{market}.{canonical}"
    yf_sym = canonical
    ef_sym = _ef_symbol_for(canonical, market)

    return SymbolInfo(
        canonical=canonical,
        market=market,
        broker_code=b_code,
        yf_symbol=yf_sym,
        ef_symbol=ef_sym,
        futu_code=b_code,
    )


def normalize_positions(
    raw_positions: list[dict],
    key_field: str = "code",
) -> list[dict]:
    """Normalize a broker position list so each row has a SymbolInfo attached.

    Args:
        raw_positions: List of dicts from broker sync or state.json.
                       Each dict must have the `key_field` (e.g. "code") that
                       contains either a broker code or canonical symbol.
        key_field:     The dict key whose value is the symbol/code to normalize.

    Returns:
        New list of dicts with an added "symbol_info" key of type SymbolInfo,
        plus normalized "canonical", "market", "broker_code" fields.
    """
    normalized = []
    for row in raw_positions:
        raw_code = str(row.get(key_field, "")).strip()
        if not raw_code:
            continue
        try:
            info = resolve_symbol_info(raw_code)
        except Exception:
            continue
        enriched = dict(row)
        enriched["symbol_info"] = info
        enriched["canonical"] = info.canonical
        enriched["market"] = info.market
        enriched["broker_code"] = info.broker_code
        normalized.append(enriched)
    return normalized
