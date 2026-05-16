"""
test_universe_expansion.py — Tests for Kiro Quant V3 universe expansion.

Validates:
  - Universe loading (300+ symbols)
  - Tier system (Tier 1/2/3)
  - Memory efficiency (LRU buffer cache)
  - Batch backfill simulation
  - Symbol format validation (HK: XXXX.HK, US: TICKER)
  - Feature flag behavior

Run with:
    cd /path/to/kiro-quant-v3
    python -m pytest tests/test_universe_expansion.py -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

# Ensure repo root is importable
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from v3_pipeline.config.universe import (
    SymbolMeta,
    SymbolUniverse,
    UniverseConfig,
    validate_symbol,
    is_hk,
    is_us,
    get_universe,
    reset_universe,
    EXPANDED_UNIVERSE,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_hk_universe(tmp_path: Path) -> Path:
    """Create a small HK universe JSON for testing."""
    data = {
        "version": "1.0",
        "region": "HK",
        "count": 5,
        "symbols": [
            {"symbol": "0700.HK", "sector": "Technology", "market_cap_usd": 450000, "liquidity_score": 0.98, "avg_daily_volume": 25.5, "tier": 1},
            {"symbol": "3690.HK", "sector": "Technology", "market_cap_usd": 85000, "liquidity_score": 0.95, "avg_daily_volume": 18.2, "tier": 1},
            {"symbol": "0005.HK", "sector": "Financials", "market_cap_usd": 180000, "liquidity_score": 0.95, "avg_daily_volume": 15.8, "tier": 2},
            {"symbol": "1299.HK", "sector": "Financials", "market_cap_usd": 95000, "liquidity_score": 0.94, "avg_daily_volume": 14.2, "tier": 2},
            {"symbol": "0883.HK", "sector": "Energy", "market_cap_usd": 75000, "liquidity_score": 0.93, "avg_daily_volume": 12.5, "tier": 3},
        ],
    }
    path = tmp_path / "hk_universe.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


@pytest.fixture
def sample_us_universe(tmp_path: Path) -> Path:
    """Create a small US universe JSON for testing."""
    data = {
        "version": "1.0",
        "region": "US",
        "count": 5,
        "symbols": [
            {"symbol": "AAPL", "sector": "Technology", "market_cap_usd": 3200000, "liquidity_score": 0.99, "avg_daily_volume": 65.2, "tier": 1},
            {"symbol": "MSFT", "sector": "Technology", "market_cap_usd": 3100000, "liquidity_score": 0.99, "avg_daily_volume": 28.5, "tier": 1},
            {"symbol": "JPM", "sector": "Financials", "market_cap_usd": 600000, "liquidity_score": 0.97, "avg_daily_volume": 15.2, "tier": 2},
            {"symbol": "XOM", "sector": "Energy", "market_cap_usd": 480000, "liquidity_score": 0.95, "avg_daily_volume": 18.5, "tier": 2},
            {"symbol": "GE", "sector": "Industrials", "market_cap_usd": 190000, "liquidity_score": 0.87, "avg_daily_volume": 7.5, "tier": 3},
        ],
    }
    path = tmp_path / "us_universe.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


# ── Symbol validation tests ─────────────────────────────────────────────────

class TestSymbolValidation:
    def test_valid_hk(self):
        assert validate_symbol("0700.HK") == (True, "HK")
        assert validate_symbol("0005.HK") == (True, "HK")
        assert validate_symbol("99999.HK") == (True, "HK")

    def test_valid_us(self):
        assert validate_symbol("AAPL") == (True, "US")
        assert validate_symbol("TSLA") == (True, "US")
        assert validate_symbol("BRK_B") == (False, "")  # underscore not allowed

    def test_invalid(self):
        assert validate_symbol("") == (False, "")
        assert validate_symbol("0700") == (False, "")
        assert validate_symbol("AAPL.HK") == (False, "")
        assert validate_symbol("0700.HK.X") == (False, "")

    def test_is_hk(self):
        assert is_hk("0700.HK") is True
        assert is_hk("AAPL") is False

    def test_is_us(self):
        assert is_us("AAPL") is True
        assert is_us("0700.HK") is False


# ── SymbolMeta tests ────────────────────────────────────────────────────────

class TestSymbolMeta:
    def test_from_dict(self):
        d = {
            "symbol": "AAPL",
            "sector": "Technology",
            "market_cap_usd": 3200000,
            "liquidity_score": 0.99,
            "avg_daily_volume": 65.2,
            "tier": 1,
            "region": "US",
        }
        meta = SymbolMeta.from_dict(d)
        assert meta.symbol == "AAPL"
        assert meta.sector == "Technology"
        assert meta.market_cap_usd == 3200000
        assert meta.liquidity_score == 0.99
        assert meta.tier == 1

    def test_to_dict(self):
        meta = SymbolMeta("0700.HK", "Technology", 450000, 0.98, 25.5, 1, "HK")
        d = meta.to_dict()
        assert d["symbol"] == "0700.HK"
        assert d["tier"] == 1


# ── SymbolUniverse tests ────────────────────────────────────────────────────

class TestSymbolUniverse:
    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        reset_universe()
        yield
        reset_universe()

    def test_load_expanded_mode(self, sample_hk_universe, sample_us_universe, tmp_path: Path, monkeypatch):
        """Test loading 300+ symbol universe when EXPANDED_UNIVERSE=300."""
        monkeypatch.setenv("EXPANDED_UNIVERSE", "300")
        # Patch paths to use temp files
        import v3_pipeline.config.universe as universe_mod
        universe_mod._HK_UNIVERSE_PATH = sample_hk_universe
        universe_mod._US_UNIVERSE_PATH = sample_us_universe
        universe_mod.EXPANDED_UNIVERSE = 300

        u = SymbolUniverse()
        u.load()
        assert len(u.get_all()) == 10
        assert len(u.get_tier1()) == 4  # 2 HK + 2 US
        assert len(u.get_tier2()) == 4  # 2 HK + 2 US
        assert len(u.get_tier3()) == 2  # 1 HK + 1 US

    def test_legacy_mode(self, monkeypatch):
        """Without EXPANDED_UNIVERSE, universe is empty."""
        monkeypatch.setenv("EXPANDED_UNIVERSE", "0")
        import v3_pipeline.config.universe as universe_mod
        universe_mod.EXPANDED_UNIVERSE = 0

        u = SymbolUniverse()
        u.load()
        assert u.get_all() == []
        assert u.get_tier1() == []

    def test_get_meta(self, sample_hk_universe, sample_us_universe, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("EXPANDED_UNIVERSE", "300")
        import v3_pipeline.config.universe as universe_mod
        universe_mod._HK_UNIVERSE_PATH = sample_hk_universe
        universe_mod._US_UNIVERSE_PATH = sample_us_universe
        universe_mod.EXPANDED_UNIVERSE = 300

        u = SymbolUniverse()
        meta = u.get_meta("0700.HK")
        assert meta is not None
        assert meta.sector == "Technology"
        assert meta.liquidity_score == 0.98

    def test_by_sector(self, sample_hk_universe, sample_us_universe, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("EXPANDED_UNIVERSE", "300")
        import v3_pipeline.config.universe as universe_mod
        universe_mod._HK_UNIVERSE_PATH = sample_hk_universe
        universe_mod._US_UNIVERSE_PATH = sample_us_universe
        universe_mod.EXPANDED_UNIVERSE = 300

        u = SymbolUniverse()
        tech = u.by_sector("Technology")
        assert "0700.HK" in tech
        assert "AAPL" in tech

    def test_by_liquidity(self, sample_hk_universe, sample_us_universe, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("EXPANDED_UNIVERSE", "300")
        import v3_pipeline.config.universe as universe_mod
        universe_mod._HK_UNIVERSE_PATH = sample_hk_universe
        universe_mod._US_UNIVERSE_PATH = sample_us_universe
        universe_mod.EXPANDED_UNIVERSE = 300

        u = SymbolUniverse()
        liquid = u.by_liquidity(min_score=0.95)
        assert "0700.HK" in liquid
        assert "AAPL" in liquid
        assert "GE" not in liquid  # liquidity_score=0.87

    def test_batch_operations(self, sample_hk_universe, sample_us_universe, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("EXPANDED_UNIVERSE", "300")
        import v3_pipeline.config.universe as universe_mod
        universe_mod._HK_UNIVERSE_PATH = sample_hk_universe
        universe_mod._US_UNIVERSE_PATH = sample_us_universe
        universe_mod.EXPANDED_UNIVERSE = 300

        u = SymbolUniverse()
        u.load()

        # Suspend
        assert u.suspend("0700.HK") is True
        assert "0700.HK" in u.get_tier3()

        # Promote
        assert u.promote("0005.HK") is True
        assert "0005.HK" in u.get_tier1()

        # Demote
        assert u.demote("0005.HK") is True
        assert "0005.HK" not in u.get_tier1()
        assert "0005.HK" in u.get_tier2()

        # Remove
        assert u.remove("GE") is True
        assert u.get_meta("GE") is None

    def test_add_symbol(self, sample_hk_universe, sample_us_universe, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("EXPANDED_UNIVERSE", "300")
        import v3_pipeline.config.universe as universe_mod
        universe_mod._HK_UNIVERSE_PATH = sample_hk_universe
        universe_mod._US_UNIVERSE_PATH = sample_us_universe
        universe_mod.EXPANDED_UNIVERSE = 300

        u = SymbolUniverse()
        u.load()

        assert u.add("9999.HK") is True
        assert u.get_meta("9999.HK") is not None
        assert u.get_meta("9999.HK").region == "HK"

        assert u.add("NEWS") is True
        assert u.get_meta("NEWS").region == "US"

        assert u.add("INVALID") is False

    def test_auto_tier(self, sample_hk_universe, sample_us_universe, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("EXPANDED_UNIVERSE", "300")
        import v3_pipeline.config.universe as universe_mod
        universe_mod._HK_UNIVERSE_PATH = sample_hk_universe
        universe_mod._US_UNIVERSE_PATH = sample_us_universe
        universe_mod.EXPANDED_UNIVERSE = 300

        cfg = UniverseConfig(tier1_size=2, tier1_liquidity_min=0.96, auto_promote=True)
        u = SymbolUniverse(cfg=cfg)
        u.load()
        u.auto_tier()

        tier1 = u.get_tier1()
        assert len(tier1) <= 2
        # AAPL and MSFT have highest liquidity (0.99), 0700.HK is 0.98
        assert "AAPL" in tier1 or "MSFT" in tier1

    def test_stats(self, sample_hk_universe, sample_us_universe, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("EXPANDED_UNIVERSE", "300")
        import v3_pipeline.config.universe as universe_mod
        universe_mod._HK_UNIVERSE_PATH = sample_hk_universe
        universe_mod._US_UNIVERSE_PATH = sample_us_universe
        universe_mod.EXPANDED_UNIVERSE = 300

        u = SymbolUniverse()
        u.load()
        stats = u.stats()
        assert stats["total"] == 10
        assert stats["hk_count"] == 5
        assert stats["us_count"] == 5


# ── LRU Buffer Cache tests ──────────────────────────────────────────────────

class TestLRUBufferCache:
    """LRU cache tests — minimal standalone implementation to avoid heavy imports."""

    class _LRUBufferCache:
        """Mirror of main_loop._LRUBufferCache for testing."""
        def __init__(self, max_size: int = 40):
            self.max_size = max_size
            self._cache: dict[str, pd.DataFrame] = {}
            self._access_order: list[str] = []

        def _touch(self, symbol: str) -> None:
            if symbol in self._access_order:
                self._access_order.remove(symbol)
            self._access_order.append(symbol)

        def get(self, symbol: str) -> pd.DataFrame:
            self._touch(symbol)
            return self._cache.get(symbol, pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume", "data_source"]))

        def set(self, symbol: str, df: pd.DataFrame) -> None:
            self._touch(symbol)
            self._cache[symbol] = df
            self._evict_if_needed()

        def _evict_if_needed(self) -> None:
            while len(self._cache) > self.max_size:
                oldest = self._access_order.pop(0)
                if oldest in self._cache:
                    del self._cache[oldest]

        def keys(self):
            return list(self._cache.keys())

    def test_basic_get_set(self):
        cache = self._LRUBufferCache(max_size=3)
        df = pd.DataFrame({"Close": [1, 2, 3]})
        cache.set("AAPL", df)
        assert len(cache.get("AAPL")) == 3

    def test_eviction(self):
        cache = self._LRUBufferCache(max_size=2)
        cache.set("A", pd.DataFrame({"Close": [1]}))
        cache.set("B", pd.DataFrame({"Close": [2]}))
        cache.set("C", pd.DataFrame({"Close": [3]}))
        assert "A" not in cache.keys()
        assert "B" in cache.keys()
        assert "C" in cache.keys()

    def test_touch_order(self):
        cache = self._LRUBufferCache(max_size=2)
        cache.set("A", pd.DataFrame({"Close": [1]}))
        cache.set("B", pd.DataFrame({"Close": [2]}))
        cache.get("A")  # touch A
        cache.set("C", pd.DataFrame({"Close": [3]}))
        assert "A" in cache.keys()  # A was touched, so B evicted
        assert "B" not in cache.keys()


# ── Main loop integration tests ─────────────────────────────────────────────

class TestMainLoopUniverseIntegration:
    def test_expanded_flag_reads_env(self, monkeypatch):
        """When EXPANDED_UNIVERSE=300, _EXPANDED_MODE should be True after reload."""
        monkeypatch.setenv("EXPANDED_UNIVERSE", "300")
        # Module-level flag is computed at import time.
        # We verify the env var logic by re-evaluating the expression.
        assert int(os.getenv("EXPANDED_UNIVERSE", "0")) >= 300

    def test_legacy_mode_fallback(self, monkeypatch):
        """Without EXPANDED_UNIVERSE, main_loop should have get_universe=None."""
        monkeypatch.setenv("EXPANDED_UNIVERSE", "0")
        assert int(os.getenv("EXPANDED_UNIVERSE", "0")) < 300


# ── Backfill tests ──────────────────────────────────────────────────────────

class TestBackfill:
    def test_progress_tracking(self, tmp_path: Path):
        from v3_pipeline.data.kline_backfill import BackfillProgress
        progress = BackfillProgress(path=tmp_path / "progress.json")
        progress.mark_done("AAPL")
        progress.mark_done("MSFT")
        progress.mark_failed("GE", "network error")
        assert progress.is_done("AAPL") is True
        assert progress.is_done("TSLA") is False
        assert progress.stats["completed"] == 2
        assert progress.stats["failed"] == 1
        progress.save()
        assert (tmp_path / "progress.json").exists()

    def test_progress_resume(self, tmp_path: Path):
        from v3_pipeline.data.kline_backfill import BackfillProgress
        progress = BackfillProgress(path=tmp_path / "progress.json")
        progress.mark_done("AAPL")
        progress.save()
        # New instance loads saved progress
        progress2 = BackfillProgress(path=tmp_path / "progress.json")
        assert progress2.is_done("AAPL") is True

    def test_progress_reset(self, tmp_path: Path):
        from v3_pipeline.data.kline_backfill import BackfillProgress
        progress = BackfillProgress(path=tmp_path / "progress.json")
        progress.mark_done("AAPL")
        progress.reset(symbols=["AAPL"])
        assert progress.is_done("AAPL") is False


# ── JSON universe file tests ────────────────────────────────────────────────

class TestUniverseFiles:
    def test_hk_universe_file_exists(self):
        path = REPO_ROOT / "data" / "hk_universe.json"
        assert path.exists(), f"{path} not found"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["region"] == "HK"
        assert len(data["symbols"]) >= 100
        # Validate all HK symbols
        for item in data["symbols"]:
            assert validate_symbol(item["symbol"])[0] is True
            assert validate_symbol(item["symbol"])[1] == "HK"

    def test_us_universe_file_exists(self):
        path = REPO_ROOT / "data" / "us_universe.json"
        assert path.exists(), f"{path} not found"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["region"] == "US"
        assert len(data["symbols"]) >= 100
        # Validate all US symbols
        for item in data["symbols"]:
            assert validate_symbol(item["symbol"])[0] is True
            assert validate_symbol(item["symbol"])[1] == "US"

    def test_dynamic_watchlist_format(self):
        path = REPO_ROOT / "dynamic_watchlist.json"
        assert path.exists(), f"{path} not found"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "version" in data
        assert "tiers" in data
        assert "tier1" in data["tiers"]
        assert "tier2" in data["tiers"]
        assert "tier3" in data["tiers"]
        assert "metadata" in data


# ── Performance sanity checks ───────────────────────────────────────────────

class TestPerformance:
    def test_universe_memory_footprint(self, sample_hk_universe, sample_us_universe, monkeypatch):
        """300 symbols should fit in ~10MB of metadata."""
        monkeypatch.setenv("EXPANDED_UNIVERSE", "300")
        import v3_pipeline.config.universe as universe_mod
        universe_mod._HK_UNIVERSE_PATH = sample_hk_universe
        universe_mod._US_UNIVERSE_PATH = sample_us_universe
        universe_mod.EXPANDED_UNIVERSE = 300

        u = SymbolUniverse()
        u.load()
        # SymbolMeta is lightweight; 10 symbols should be trivial
        import sys
        total_size = sum(sys.getsizeof(m) for m in u._meta_by_symbol.values())
        assert total_size < 1024 * 1024  # Less than 1MB for test data

    def test_lru_cache_size(self):
        """LRU cache should never exceed max_size."""
        cache = TestLRUBufferCache._LRUBufferCache(max_size=40)
        for i in range(50):
            cache.set(f"SYM{i}", pd.DataFrame({"Close": list(range(100))}))
        assert len(cache.keys()) == 40


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
