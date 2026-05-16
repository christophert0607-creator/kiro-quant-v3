"""
universe.py — Symbol universe management for Kiro Quant V3.

Defines 300+ symbols (150 HK + 150+ US) with sector/market-cap/liquidity metadata.
Supports tiered watchlist operations and symbol validation.

Usage:
    from v3_pipeline.config.universe import SymbolUniverse
    universe = SymbolUniverse()
    active = universe.get_tier1()      # ~20 symbols for active trading
    full = universe.get_tier2()        # 300+ symbols for data collection
    suspended = universe.get_tier3()   # Suspended/removed symbols

Feature flag: set EXPANDED_UNIVERSE=300 to enable 300+ mode.
Without the flag, falls back to legacy 69-symbol mode.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kiro.universe")

# ── Feature flag ─────────────────────────────────────────────────────────────
EXPANDED_UNIVERSE = int(os.getenv("EXPANDED_UNIVERSE", "0"))

# ── Paths ────────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent.parent
_HK_UNIVERSE_PATH = _REPO_ROOT / "data" / "hk_universe.json"
_US_UNIVERSE_PATH = _REPO_ROOT / "data" / "us_universe.json"
_WATCHLIST_PATH = _REPO_ROOT / "dynamic_watchlist.json"


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SymbolMeta:
    """Metadata for a single symbol in the universe."""
    symbol: str
    sector: str
    market_cap_usd: float  # in millions USD
    liquidity_score: float  # 0.0–1.0, higher = more liquid
    avg_daily_volume: float  # in millions
    tier: int = 2  # 1=active trading, 2=data collection, 3=suspended
    region: str = ""  # "HK" or "US"
    last_updated: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "sector": self.sector,
            "market_cap_usd": self.market_cap_usd,
            "liquidity_score": self.liquidity_score,
            "avg_daily_volume": self.avg_daily_volume,
            "tier": self.tier,
            "region": self.region,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SymbolMeta":
        return cls(
            symbol=d["symbol"],
            sector=d.get("sector", "Unknown"),
            market_cap_usd=float(d.get("market_cap_usd", 0)),
            liquidity_score=float(d.get("liquidity_score", 0.5)),
            avg_daily_volume=float(d.get("avg_daily_volume", 0)),
            tier=int(d.get("tier", 2)),
            region=d.get("region", ""),
            last_updated=d.get("last_updated", ""),
        )


@dataclass
class UniverseConfig:
    """Configuration for universe behavior."""
    tier1_size: int = 20          # Max active trading symbols
    tier1_liquidity_min: float = 0.6  # Min liquidity score for Tier 1
    tier1_market_cap_min: float = 5000  # Min market cap (M USD) for Tier 1
    auto_promote: bool = True     # Auto-promote high-liquidity symbols to Tier 1
    auto_suspend_stale_days: int = 7  # Auto-suspend if no data for N days


# ── Symbol validation ────────────────────────────────────────────────────────

_HK_RE = re.compile(r"^\d{4,5}\.HK$")
_US_RE = re.compile(r"^[A-Z]{1,5}$")


def validate_symbol(symbol: str) -> tuple[bool, str]:
    """Validate symbol format. Returns (is_valid, region)."""
    if _HK_RE.match(symbol):
        return True, "HK"
    if _US_RE.match(symbol):
        return True, "US"
    return False, ""


def is_hk(symbol: str) -> bool:
    return bool(_HK_RE.match(symbol))


def is_us(symbol: str) -> bool:
    return bool(_US_RE.match(symbol))


# ── Universe loader ──────────────────────────────────────────────────────────

def _load_json_universe(path: Path) -> list[SymbolMeta]:
    """Load symbol metadata from a JSON file."""
    if not path.exists():
        logger.warning("[universe] Universe file not found: %s", path)
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        symbols = []
        for item in data.get("symbols", []):
            meta = SymbolMeta.from_dict(item)
            symbols.append(meta)
        logger.info("[universe] Loaded %d symbols from %s", len(symbols), path.name)
        return symbols
    except Exception as exc:
        logger.error("[universe] Failed to load %s: %s", path.name, exc)
        return []


class SymbolUniverse:
    """
    Central symbol universe manager.

    • Lazy-loads symbol metadata from JSON files.
    • Supports tiered watchlist (Tier 1/2/3).
    • Validates symbol formats.
    • Batch operations: add, remove, suspend, promote.
    """

    def __init__(self, cfg: Optional[UniverseConfig] = None):
        self.cfg = cfg or UniverseConfig()
        self._meta_by_symbol: dict[str, SymbolMeta] = {}
        self._tier1: set[str] = set()
        self._tier3: set[str] = set()
        self._loaded = False

    # ── Loading ─────────────────────────────────────────────────────────────

    def load(self) -> "SymbolUniverse":
        """Load universe from JSON files. Idempotent."""
        if self._loaded:
            return self
        self._loaded = True

        if EXPANDED_UNIVERSE < 300:
            logger.info("[universe] EXPANDED_UNIVERSE=%s — skipping full universe load", EXPANDED_UNIVERSE)
            return self

        hk = _load_json_universe(_HK_UNIVERSE_PATH)
        us = _load_json_universe(_US_UNIVERSE_PATH)

        for meta in hk + us:
            self._meta_by_symbol[meta.symbol] = meta
            if meta.tier == 1:
                self._tier1.add(meta.symbol)
            elif meta.tier == 3:
                self._tier3.add(meta.symbol)

        logger.info(
            "[universe] Loaded %d symbols (HK=%d, US=%d, Tier1=%d, Tier3=%d)",
            len(self._meta_by_symbol),
            len(hk), len(us),
            len(self._tier1), len(self._tier3),
        )
        return self

    # ── Tier queries ────────────────────────────────────────────────────────

    def get_tier1(self) -> list[str]:
        """Active trading symbols (screener picks, ~20)."""
        self.load()
        if not self._meta_by_symbol:
            return []
        return sorted(self._tier1)

    def get_tier2(self) -> list[str]:
        """Full universe for data collection (300+)."""
        self.load()
        if not self._meta_by_symbol:
            return []
        return sorted(
            s for s, m in self._meta_by_symbol.items()
            if m.tier == 2
        )

    def get_tier3(self) -> list[str]:
        """Suspended/removed symbols."""
        self.load()
        return sorted(self._tier3)

    def get_all(self) -> list[str]:
        """All symbols in the universe."""
        self.load()
        return sorted(self._meta_by_symbol.keys())

    def get_active_trading(self) -> list[str]:
        """Tier 1 only — symbols that should be actively traded."""
        return self.get_tier1()

    def get_data_collection(self) -> list[str]:
        """Tier 1 + Tier 2 — symbols that need data collection."""
        self.load()
        return sorted(
            s for s, m in self._meta_by_symbol.items()
            if m.tier in (1, 2)
        )

    # ── Metadata queries ───────────────────────────────────────────────────

    def get_meta(self, symbol: str) -> Optional[SymbolMeta]:
        self.load()
        return self._meta_by_symbol.get(symbol)

    def get_sector(self, symbol: str) -> str:
        meta = self.get_meta(symbol)
        return meta.sector if meta else "Unknown"

    def get_liquidity(self, symbol: str) -> float:
        meta = self.get_meta(symbol)
        return meta.liquidity_score if meta else 0.0

    def by_sector(self, sector: str) -> list[str]:
        """Return symbols in a given sector."""
        self.load()
        return sorted(
            s for s, m in self._meta_by_symbol.items()
            if m.sector.lower() == sector.lower()
        )

    def by_liquidity(self, min_score: float = 0.5) -> list[str]:
        """Return symbols with liquidity score >= threshold."""
        self.load()
        return sorted(
            s for s, m in self._meta_by_symbol.items()
            if m.liquidity_score >= min_score
        )

    # ── Batch operations ────────────────────────────────────────────────────

    def add(self, symbol: str, meta: Optional[SymbolMeta] = None) -> bool:
        """Add a symbol to the universe (Tier 2 by default)."""
        valid, region = validate_symbol(symbol)
        if not valid:
            logger.warning("[universe] Invalid symbol format: %s", symbol)
            return False
        if meta is None:
            meta = SymbolMeta(
                symbol=symbol,
                sector="Unknown",
                market_cap_usd=0.0,
                liquidity_score=0.5,
                avg_daily_volume=0.0,
                tier=2,
                region=region,
            )
        self._meta_by_symbol[symbol] = meta
        logger.info("[universe] Added %s (tier=%d, region=%s)", symbol, meta.tier, region)
        return True

    def remove(self, symbol: str) -> bool:
        """Permanently remove a symbol from the universe."""
        if symbol not in self._meta_by_symbol:
            return False
        del self._meta_by_symbol[symbol]
        self._tier1.discard(symbol)
        self._tier3.discard(symbol)
        logger.info("[universe] Removed %s", symbol)
        return True

    def suspend(self, symbol: str) -> bool:
        """Move symbol to Tier 3 (suspended)."""
        meta = self._meta_by_symbol.get(symbol)
        if meta is None:
            return False
        # Create new frozen dataclass with updated tier
        updated = SymbolMeta(
            symbol=meta.symbol,
            sector=meta.sector,
            market_cap_usd=meta.market_cap_usd,
            liquidity_score=meta.liquidity_score,
            avg_daily_volume=meta.avg_daily_volume,
            tier=3,
            region=meta.region,
            last_updated=meta.last_updated,
        )
        self._meta_by_symbol[symbol] = updated
        self._tier1.discard(symbol)
        self._tier3.add(symbol)
        logger.info("[universe] Suspended %s → Tier 3", symbol)
        return True

    def promote(self, symbol: str) -> bool:
        """Promote symbol to Tier 1 (active trading)."""
        meta = self._meta_by_symbol.get(symbol)
        if meta is None:
            return False
        updated = SymbolMeta(
            symbol=meta.symbol,
            sector=meta.sector,
            market_cap_usd=meta.market_cap_usd,
            liquidity_score=meta.liquidity_score,
            avg_daily_volume=meta.avg_daily_volume,
            tier=1,
            region=meta.region,
            last_updated=meta.last_updated,
        )
        self._meta_by_symbol[symbol] = updated
        self._tier1.add(symbol)
        self._tier3.discard(symbol)
        logger.info("[universe] Promoted %s → Tier 1", symbol)
        return True

    def demote(self, symbol: str) -> bool:
        """Demote symbol to Tier 2 (data collection only)."""
        meta = self._meta_by_symbol.get(symbol)
        if meta is None:
            return False
        updated = SymbolMeta(
            symbol=meta.symbol,
            sector=meta.sector,
            market_cap_usd=meta.market_cap_usd,
            liquidity_score=meta.liquidity_score,
            avg_daily_volume=meta.avg_daily_volume,
            tier=2,
            region=meta.region,
            last_updated=meta.last_updated,
        )
        self._meta_by_symbol[symbol] = updated
        self._tier1.discard(symbol)
        self._tier3.discard(symbol)
        logger.info("[universe] Demoted %s → Tier 2", symbol)
        return True

    def auto_tier(self) -> None:
        """Auto-assign tiers based on liquidity and market cap rules."""
        if not self.cfg.auto_promote:
            return
        candidates = []
        for symbol, meta in self._meta_by_symbol.items():
            if meta.tier == 3:
                continue
            if (meta.liquidity_score >= self.cfg.tier1_liquidity_min and
                meta.market_cap_usd >= self.cfg.tier1_market_cap_min):
                candidates.append((symbol, meta.liquidity_score))
        # Sort by liquidity desc, take top N
        candidates.sort(key=lambda x: -x[1])
        new_tier1 = {s for s, _ in candidates[: self.cfg.tier1_size]}
        for symbol in new_tier1:
            if symbol not in self._tier1:
                self.promote(symbol)
        # Demote old Tier 1 that fell out
        for symbol in list(self._tier1):
            if symbol not in new_tier1:
                self.demote(symbol)

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> None:
        """Save current universe state back to JSON."""
        target = path or _WATCHLIST_PATH
        data = {
            "version": "2.0",
            "expanded_universe": EXPANDED_UNIVERSE,
            "total_symbols": len(self._meta_by_symbol),
            "tier1_count": len(self._tier1),
            "tier2_count": len(self.get_tier2()),
            "tier3_count": len(self._tier3),
            "last_updated": str(pd.Timestamp.now(tz="UTC")),
            "symbols": [m.to_dict() for m in self._meta_by_symbol.values()],
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("[universe] Saved %d symbols to %s", len(self._meta_by_symbol), target)

    # ── Legacy compatibility ────────────────────────────────────────────────

    @staticmethod
    def from_legacy_watchlist(path: Path = _WATCHLIST_PATH) -> list[str]:
        """Load symbols from legacy dynamic_watchlist.json (picks array)."""
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            picks = data.get("picks", [])
            if isinstance(picks, list):
                return picks
            return []
        except Exception as exc:
            logger.warning("[universe] Failed to load legacy watchlist: %s", exc)
            return []

    # ── Stats ───────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        self.load()
        sectors: dict[str, int] = {}
        for m in self._meta_by_symbol.values():
            sectors[m.sector] = sectors.get(m.sector, 0) + 1
        return {
            "total": len(self._meta_by_symbol),
            "tier1": len(self._tier1),
            "tier2": len(self.get_tier2()),
            "tier3": len(self._tier3),
            "hk_count": len([s for s in self._meta_by_symbol if is_hk(s)]),
            "us_count": len([s for s in self._meta_by_symbol if is_us(s)]),
            "sectors": sectors,
        }


# ── Convenience singleton ────────────────────────────────────────────────────

_universe_instance: Optional[SymbolUniverse] = None


def get_universe() -> SymbolUniverse:
    """Return the global SymbolUniverse singleton."""
    global _universe_instance
    if _universe_instance is None:
        _universe_instance = SymbolUniverse()
        _universe_instance.load()
    return _universe_instance


def reset_universe() -> None:
    """Reset the global singleton (useful for testing)."""
    global _universe_instance
    _universe_instance = None
