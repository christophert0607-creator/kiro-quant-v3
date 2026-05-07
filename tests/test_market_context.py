"""Tests for v3_pipeline.core.market_context (Phase 3 - market isolation)."""
import pytest

from v3_pipeline.core.market_context import (
    MARKET_HK,
    MARKET_US,
    MarketContext,
    broker_code,
    build_market_contexts,
    infer_market,
    sync_positions_to_contexts,
)


# ── infer_market ──────────────────────────────────────────────────────────────

class TestInferMarket:
    def test_hk_suffix(self):
        assert infer_market("0700.HK") == MARKET_HK

    def test_hk_suffix_case_insensitive(self):
        assert infer_market("0700.hk") == MARKET_HK

    def test_us_bare_symbol(self):
        assert infer_market("TSLA") == MARKET_US

    def test_us_dotted_suffix(self):
        assert infer_market("TSLA.US") == MARKET_US

    def test_index_symbol(self):
        assert infer_market("SPY") == MARKET_US

    def test_hk_index(self):
        assert infer_market("HSI.HK") == MARKET_HK

    def test_empty_string_defaults_us(self):
        assert infer_market("") == MARKET_US


# ── broker_code ───────────────────────────────────────────────────────────────

class TestBrokerCode:
    def test_hk_symbol(self):
        assert broker_code("0700.HK") == "HK.0700.HK"

    def test_us_symbol(self):
        assert broker_code("TSLA") == "US.TSLA"

    def test_explicit_market_override(self):
        assert broker_code("TSLA", market="US") == "US.TSLA"
        assert broker_code("0700.HK", market="HK") == "HK.0700.HK"


# ── MarketContext construction ─────────────────────────────────────────────────

class TestMarketContext:
    def test_market_is_uppercased(self):
        ctx = MarketContext(market="hk")
        assert ctx.market == "HK"

    def test_invalid_market_raises(self):
        with pytest.raises(ValueError, match="Unsupported market"):
            MarketContext(market="CN")

    def test_add_symbol_idempotent(self):
        ctx = MarketContext(market="US")
        ctx.add_symbol("TSLA")
        ctx.add_symbol("TSLA")
        assert ctx.symbols.count("TSLA") == 1

    def test_position_defaults_to_zero(self):
        ctx = MarketContext(market="US")
        ctx.add_symbol("TSLA")
        assert ctx.long_qty("TSLA") == 0
        assert ctx.short_qty("TSLA") == 0

    def test_broker_code_for(self):
        ctx = MarketContext(market="HK")
        assert ctx.broker_code_for("0700.HK") == "HK.0700.HK"

    def test_active_long_symbols(self):
        ctx = MarketContext(market="US")
        ctx.add_symbol("TSLA")
        ctx.add_symbol("NVDA")
        ctx.position_qty["TSLA"] = 100
        assert ctx.active_long_symbols() == ["TSLA"]

    def test_active_short_symbols(self):
        ctx = MarketContext(market="US")
        ctx.add_symbol("NVDA")
        ctx.short_position_qty["NVDA"] = 50
        assert ctx.active_short_symbols() == ["NVDA"]

    def test_total_active_positions(self):
        ctx = MarketContext(market="US")
        ctx.add_symbol("TSLA")
        ctx.add_symbol("NVDA")
        ctx.add_symbol("AAPL")
        ctx.position_qty["TSLA"] = 100
        ctx.short_position_qty["NVDA"] = 50
        assert ctx.total_active_positions() == 2


# ── build_market_contexts ──────────────────────────────────────────────────────

class TestBuildMarketContexts:
    def test_single_market_us(self):
        contexts = build_market_contexts(["TSLA", "NVDA", "AAPL"])
        assert set(contexts.keys()) == {"US"}
        assert set(contexts["US"].symbols) == {"TSLA", "NVDA", "AAPL"}

    def test_single_market_hk(self):
        contexts = build_market_contexts(["0700.HK", "9988.HK"])
        assert set(contexts.keys()) == {"HK"}
        assert "0700.HK" in contexts["HK"].symbols
        assert "9988.HK" in contexts["HK"].symbols

    def test_mixed_hk_and_us(self):
        symbols = ["0700.HK", "TSLA", "9988.HK", "NVDA"]
        contexts = build_market_contexts(symbols)
        assert set(contexts.keys()) == {"HK", "US"}
        assert "0700.HK" in contexts["HK"].symbols
        assert "9988.HK" in contexts["HK"].symbols
        assert "TSLA" in contexts["US"].symbols
        assert "NVDA" in contexts["US"].symbols

    def test_account_ids_routed_per_market(self):
        symbols = ["0700.HK", "TSLA"]
        contexts = build_market_contexts(symbols, account_ids={"HK": 111, "US": 222})
        assert contexts["HK"].account_id == 111
        assert contexts["US"].account_id == 222

    def test_missing_account_id_is_none(self):
        contexts = build_market_contexts(["TSLA"])
        assert contexts["US"].account_id is None

    def test_empty_symbol_list(self):
        contexts = build_market_contexts([])
        assert contexts == {}

    def test_hk_symbols_not_in_us_context(self):
        contexts = build_market_contexts(["0700.HK", "TSLA"])
        assert "0700.HK" not in contexts.get("US", MarketContext(market="US")).symbols
        assert "TSLA" not in contexts.get("HK", MarketContext(market="HK")).symbols

    def test_positions_initialised_to_zero(self):
        contexts = build_market_contexts(["TSLA", "NVDA"])
        for sym in ["TSLA", "NVDA"]:
            assert contexts["US"].long_qty(sym) == 0
            assert contexts["US"].short_qty(sym) == 0


# ── sync_positions_to_contexts ─────────────────────────────────────────────────

class TestSyncPositionsToContexts:
    def _make_contexts(self):
        return build_market_contexts(["0700.HK", "TSLA", "NVDA"])

    def test_long_position_applied(self):
        contexts = self._make_contexts()
        sync_positions_to_contexts(contexts, [{"code": "US.TSLA", "qty": 200}])
        assert contexts["US"].long_qty("TSLA") == 200
        assert contexts["US"].short_qty("TSLA") == 0

    def test_short_position_applied(self):
        contexts = self._make_contexts()
        sync_positions_to_contexts(contexts, [{"code": "US.NVDA", "qty": -50}])
        assert contexts["US"].short_qty("NVDA") == 50
        assert contexts["US"].long_qty("NVDA") == 0

    def test_hk_position_applied(self):
        contexts = self._make_contexts()
        sync_positions_to_contexts(contexts, [{"code": "HK.0700.HK", "qty": 1000}])
        assert contexts["HK"].long_qty("0700.HK") == 1000

    def test_unknown_market_in_broker_data_skipped(self):
        contexts = self._make_contexts()
        sync_positions_to_contexts(contexts, [{"code": "CN.600519", "qty": 100}])
        # No exception, no side-effects
        assert contexts["US"].total_active_positions() == 0
        assert contexts["HK"].total_active_positions() == 0

    def test_symbol_not_in_context_skipped(self):
        contexts = self._make_contexts()
        sync_positions_to_contexts(contexts, [{"code": "US.GME", "qty": 100}])
        assert contexts["US"].total_active_positions() == 0

    def test_malformed_code_skipped(self):
        contexts = self._make_contexts()
        sync_positions_to_contexts(contexts, [{"code": "BADCODE", "qty": 100}])
        assert contexts["US"].total_active_positions() == 0

    def test_multiple_positions_applied(self):
        contexts = self._make_contexts()
        sync_positions_to_contexts(contexts, [
            {"code": "US.TSLA", "qty": 100},
            {"code": "US.NVDA", "qty": -30},
            {"code": "HK.0700.HK", "qty": 500},
        ])
        assert contexts["US"].long_qty("TSLA") == 100
        assert contexts["US"].short_qty("NVDA") == 30
        assert contexts["HK"].long_qty("0700.HK") == 500
