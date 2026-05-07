"""Tests for v3_pipeline.core.symbol_info (P1 — position sync normalization)."""
import pytest

from v3_pipeline.core.symbol_info import (
    MARKET_HK,
    MARKET_US,
    SymbolInfo,
    normalize_positions,
    resolve_symbol_info,
)


# ── resolve_symbol_info: HK symbols ───────────────────────────────────────────

class TestResolveHKSymbols:
    def test_canonical_hk_symbol(self):
        info = resolve_symbol_info("0700.HK")
        assert info.canonical == "0700.HK"
        assert info.market == MARKET_HK
        assert info.broker_code == "HK.0700.HK"
        assert info.futu_code == "HK.0700.HK"

    def test_broker_code_input_stripped(self):
        info = resolve_symbol_info("HK.0700.HK")
        assert info.canonical == "0700.HK"
        assert info.market == MARKET_HK

    def test_hk_case_insensitive(self):
        info = resolve_symbol_info("0700.hk")
        assert info.market == MARKET_HK

    def test_hk_yf_symbol_matches_canonical(self):
        info = resolve_symbol_info("0700.HK")
        assert info.yf_symbol == "0700.HK"

    def test_hk_ef_symbol_is_5digit_code(self):
        info = resolve_symbol_info("0700.HK")
        assert info.ef_symbol == "00700"

    def test_hk_ef_symbol_pads_leading_zeros(self):
        info = resolve_symbol_info("9988.HK")
        assert info.ef_symbol == "09988"

    def test_hk_ef_symbol_small_code(self):
        info = resolve_symbol_info("0001.HK")
        assert info.ef_symbol == "00001"

    def test_hk_is_hk_method(self):
        info = resolve_symbol_info("0700.HK")
        assert info.is_hk()
        assert not info.is_us()


# ── resolve_symbol_info: US symbols ───────────────────────────────────────────

class TestResolveUSSymbols:
    def test_bare_us_symbol(self):
        info = resolve_symbol_info("TSLA")
        assert info.canonical == "TSLA"
        assert info.market == MARKET_US
        assert info.broker_code == "US.TSLA"

    def test_broker_code_input_stripped(self):
        info = resolve_symbol_info("US.TSLA")
        assert info.canonical == "TSLA"
        assert info.market == MARKET_US

    def test_us_yf_symbol_matches_canonical(self):
        info = resolve_symbol_info("NVDA")
        assert info.yf_symbol == "NVDA"

    def test_us_ef_symbol_matches_canonical(self):
        info = resolve_symbol_info("AAPL")
        assert info.ef_symbol == "AAPL"

    def test_us_is_us_method(self):
        info = resolve_symbol_info("TSLA")
        assert info.is_us()
        assert not info.is_hk()


# ── resolve_symbol_info: market_override ─────────────────────────────────────

class TestMarketOverride:
    def test_override_to_us(self):
        info = resolve_symbol_info("SOME_SYMBOL", market_override="US")
        assert info.market == MARKET_US

    def test_override_to_hk(self):
        info = resolve_symbol_info("0700", market_override="HK")
        assert info.market == MARKET_HK

    def test_override_case_insensitive(self):
        info = resolve_symbol_info("TSLA", market_override="us")
        assert info.market == MARKET_US

    def test_invalid_market_override_raises(self):
        with pytest.raises(ValueError, match="Unsupported market"):
            resolve_symbol_info("TSLA", market_override="CN")


# ── SymbolInfo frozenness and equality ────────────────────────────────────────

class TestSymbolInfoProperties:
    def test_frozen_prevents_mutation(self):
        info = resolve_symbol_info("TSLA")
        with pytest.raises(Exception):
            info.canonical = "NVDA"  # type: ignore[misc]

    def test_equal_same_symbol(self):
        assert resolve_symbol_info("TSLA") == resolve_symbol_info("TSLA")

    def test_not_equal_different_symbols(self):
        assert resolve_symbol_info("TSLA") != resolve_symbol_info("NVDA")

    def test_hk_broker_code_never_us_prefixed(self):
        info = resolve_symbol_info("0700.HK")
        assert not info.broker_code.startswith("US.")

    def test_us_broker_code_never_hk_prefixed(self):
        info = resolve_symbol_info("TSLA")
        assert not info.broker_code.startswith("HK.")


# ── normalize_positions ───────────────────────────────────────────────────────

class TestNormalizePositions:
    def test_basic_normalization(self):
        rows = [{"code": "HK.0700.HK", "qty": 100}]
        result = normalize_positions(rows)
        assert len(result) == 1
        assert result[0]["canonical"] == "0700.HK"
        assert result[0]["market"] == MARKET_HK
        assert result[0]["broker_code"] == "HK.0700.HK"
        assert isinstance(result[0]["symbol_info"], SymbolInfo)

    def test_mixed_hk_us_positions(self):
        rows = [
            {"code": "HK.0700.HK", "qty": 100},
            {"code": "US.TSLA", "qty": 50},
        ]
        result = normalize_positions(rows)
        assert len(result) == 2
        markets = {r["market"] for r in result}
        assert markets == {MARKET_HK, MARKET_US}

    def test_empty_input(self):
        assert normalize_positions([]) == []

    def test_missing_code_field_skipped(self):
        rows = [{"qty": 100}]
        result = normalize_positions(rows)
        assert result == []

    def test_original_fields_preserved(self):
        rows = [{"code": "US.NVDA", "qty": 30, "pnl": -5.5}]
        result = normalize_positions(rows)
        assert result[0]["qty"] == 30
        assert result[0]["pnl"] == -5.5

    def test_custom_key_field(self):
        rows = [{"symbol": "US.AAPL", "qty": 10}]
        result = normalize_positions(rows, key_field="symbol")
        assert result[0]["canonical"] == "AAPL"

    def test_malformed_or_empty_code_skipped(self):
        rows = [{"code": "", "qty": 1}, {"code": "   ", "qty": 2}]
        result = normalize_positions(rows)
        assert result == []

    def test_canonical_symbols_also_work(self):
        rows = [{"code": "TSLA", "qty": 5}]
        result = normalize_positions(rows)
        assert result[0]["canonical"] == "TSLA"
        assert result[0]["market"] == MARKET_US

    def test_hk_canonical_as_input(self):
        rows = [{"code": "0700.HK", "qty": 200}]
        result = normalize_positions(rows)
        assert result[0]["canonical"] == "0700.HK"
        assert result[0]["broker_code"] == "HK.0700.HK"
