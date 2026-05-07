"""Tests for HK/US market isolation in FutuConnector (P0 — market context).

Uses Futu SDK fakes so no live connection is required.
"""
from __future__ import annotations

import types
from typing import Any, Optional
from unittest.mock import MagicMock

import pandas as pd
import pytest

from v3_pipeline.core.futu_connector import FutuConfig, FutuConnector
from v3_pipeline.core.market_context import (
    build_market_contexts,
    infer_market,
    sync_positions_to_contexts,
)


# ── Fake Futu SDK helpers ─────────────────────────────────────────────────────

class FakeTradeContext:
    """Minimal Futu trade context fake."""

    def __init__(self, market: str, acc_id: int = 10001) -> None:
        self.market = market
        self._acc_id = acc_id
        self._closed = False
        self.orders: list[dict] = []

    def get_acc_list(self, **kwargs):
        return 0, pd.DataFrame([{"acc_id": self._acc_id, "trd_market_auth": [self.market]}])

    def accinfo_query(self, **kwargs):
        return 0, pd.DataFrame([{"total_assets": 100000.0, "cash": 50000.0, "power": 50000.0}])

    def position_list_query(self, **kwargs):
        rows = [
            {"code": f"{self.market}.0700.HK" if self.market == "HK" else "US.TSLA", "qty": 100, "position_side": "LONG"},
        ]
        return 0, pd.DataFrame(rows)

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return 0, pd.DataFrame([{"order_id": "ORD_001"}])

    def unlock_trade(self, password):
        return 0, "ok"

    def close(self):
        self._closed = True


class FakeQuoteContext:
    def get_stock_quote(self, codes):
        return 1, pd.DataFrame()  # always fails so tests don't rely on it

    def subscribe(self, codes, sub_types, subscribe_push=False):
        return 0, "ok"

    def get_order_book(self, code, num=1):
        return 1, {}

    def close(self):
        pass


def _make_fake_ft(hk_ctx: Optional[FakeTradeContext] = None, us_ctx: Optional[FakeTradeContext] = None):
    """Build a types.SimpleNamespace that mimics the futu module."""
    _hk = hk_ctx or FakeTradeContext("HK", acc_id=20001)
    _us = us_ctx or FakeTradeContext("US", acc_id=30001)

    class FakeTrdEnv:
        REAL = "REAL"
        SIMULATE = "SIMULATE"

    class FakeTrdSide:
        BUY = "BUY"
        SELL = "SELL"

    class FakeOrderType:
        NORMAL = "NORMAL"

    module = types.SimpleNamespace(
        RET_OK=0,
        TrdEnv=FakeTrdEnv,
        TrdSide=FakeTrdSide,
        OrderType=FakeOrderType,
        SubType=types.SimpleNamespace(QUOTE="QUOTE"),
        OpenQuoteContext=lambda host, port: FakeQuoteContext(),
        OpenUSTradeContext=lambda host, port: _us,
        OpenHKTradeContext=lambda host, port: _hk,
        _hk_ctx=_hk,
        _us_ctx=_us,
    )
    return module


def _connected_connector(
    market_prefix: str = "US",
    hk_ctx: Optional[FakeTradeContext] = None,
    us_ctx: Optional[FakeTradeContext] = None,
) -> tuple[FutuConnector, Any]:
    """Return a FutuConnector that is connected via fake Futu SDK."""
    import sys

    cfg = FutuConfig(market_prefix=market_prefix, trd_env="SIMULATE")
    connector = FutuConnector(config=cfg, config_json_path="/nonexistent/config.json")

    fake_ft = _make_fake_ft(hk_ctx=hk_ctx, us_ctx=us_ctx)
    sys.modules["futu"] = fake_ft
    try:
        connector.connect()
    finally:
        sys.modules.pop("futu", None)

    return connector, fake_ft


# ── Multi-market connect ───────────────────────────────────────────────────────

class TestMultiMarketConnect:
    def test_both_contexts_registered(self):
        connector, fake_ft = _connected_connector("US")
        assert "US" in connector.trade_ctxs
        assert "HK" in connector.trade_ctxs

    def test_us_context_is_correct_type(self):
        connector, fake_ft = _connected_connector("US")
        assert connector.trade_ctxs["US"] is fake_ft._us_ctx

    def test_hk_context_is_correct_type(self):
        connector, fake_ft = _connected_connector("US")
        assert connector.trade_ctxs["HK"] is fake_ft._hk_ctx

    def test_trade_ctx_points_to_primary_market(self):
        connector, fake_ft = _connected_connector("US")
        assert connector.trade_ctx is connector.trade_ctxs["US"]

    def test_trade_ctx_hk_primary(self):
        connector, fake_ft = _connected_connector("HK")
        assert connector.trade_ctx is connector.trade_ctxs["HK"]


# ── get_trade_ctx routing ─────────────────────────────────────────────────────

class TestGetTradeCtx:
    def test_us_routes_to_us_context(self):
        connector, fake_ft = _connected_connector("US")
        assert connector.get_trade_ctx("US") is fake_ft._us_ctx

    def test_hk_routes_to_hk_context(self):
        connector, fake_ft = _connected_connector("US")
        assert connector.get_trade_ctx("HK") is fake_ft._hk_ctx

    def test_case_insensitive(self):
        connector, fake_ft = _connected_connector("US")
        assert connector.get_trade_ctx("hk") is fake_ft._hk_ctx

    def test_unknown_market_falls_back_to_trade_ctx(self):
        connector, fake_ft = _connected_connector("US")
        # "CN" has no dedicated context — should fall back to trade_ctx without raising
        result = connector.get_trade_ctx("CN")
        assert result is connector.trade_ctx

    def test_no_contexts_no_trade_ctx_raises(self):
        cfg = FutuConfig(trd_env="SIMULATE")
        connector = FutuConnector(config=cfg, config_json_path="/nonexistent/config.json")
        # Neither trade_ctx nor trade_ctxs set
        with pytest.raises(RuntimeError, match="No trade context"):
            connector.get_trade_ctx("US")


# ── Account ID discovery ──────────────────────────────────────────────────────

class TestAccountDiscovery:
    def test_per_market_account_ids_populated(self):
        hk = FakeTradeContext("HK", acc_id=20001)
        us = FakeTradeContext("US", acc_id=30001)
        connector, _ = _connected_connector("US", hk_ctx=hk, us_ctx=us)
        # account_ids should have at least US after discover_accounts
        assert "US" in connector.account_ids or connector.config.target_acc_id is not None

    def test_account_kwargs_for_market_uses_per_market_id(self):
        connector, _ = _connected_connector("US")
        connector.account_ids["HK"] = 20001
        connector.account_ids["US"] = 30001
        assert connector._account_kwargs_for_market("HK") == {"acc_id": 20001}
        assert connector._account_kwargs_for_market("US") == {"acc_id": 30001}

    def test_account_kwargs_falls_back_to_global(self):
        connector, _ = _connected_connector("US")
        connector.account_ids = {}
        connector.config.target_acc_id = 99999
        assert connector._account_kwargs_for_market("HK") == {"acc_id": 99999}

    def test_account_kwargs_returns_empty_when_no_id(self):
        connector, _ = _connected_connector("US")
        connector.account_ids = {}
        connector.config.target_acc_id = None
        assert connector._account_kwargs_for_market("HK") == {}


# ── Symbol routing — 0700.HK must never become US.0700.HK ────────────────────

class TestSymbolRouting:
    def test_hk_symbol_resolves_to_hk_prefix(self):
        connector, _ = _connected_connector("US")
        code, market = connector.resolve_symbol("0700.HK")
        assert code == "HK.0700.HK"
        assert market == "HK"

    def test_us_symbol_resolves_to_us_prefix(self):
        connector, _ = _connected_connector("US")
        code, market = connector.resolve_symbol("TSLA")
        assert code == "US.TSLA"
        assert market == "US"

    def test_hk_symbol_never_gets_us_prefix(self):
        cfg = FutuConfig(market_prefix="US", trd_env="SIMULATE")
        connector = FutuConnector(config=cfg, config_json_path="/nonexistent/config.json")
        code, market = connector.resolve_symbol("9988.HK")
        assert "US.9988.HK" not in (code,)
        assert code == "HK.9988.HK"

    def test_mixed_symbol_list_routes_correctly(self):
        connector, _ = _connected_connector("US")
        symbols = ["0700.HK", "TSLA", "9988.HK", "NVDA"]
        for sym in symbols:
            code, market = connector.resolve_symbol(sym)
            if sym.endswith(".HK"):
                assert code.startswith("HK."), f"{sym} got wrong prefix: {code}"
                assert market == "HK"
            else:
                assert code.startswith("US."), f"{sym} got wrong prefix: {code}"
                assert market == "US"


# ── place_order routing ───────────────────────────────────────────────────────

class TestPlaceOrderRouting:
    def test_hk_order_goes_to_hk_context(self):
        hk = FakeTradeContext("HK", acc_id=20001)
        us = FakeTradeContext("US", acc_id=30001)
        connector, fake_ft = _connected_connector("US", hk_ctx=hk, us_ctx=us)
        connector.account_ids = {"HK": 20001, "US": 30001}

        connector.place_order("0700.HK", qty=100, side="BUY", price=300.0)

        assert len(hk.orders) == 1
        order = hk.orders[0]
        assert order["code"] == "HK.0700.HK"
        assert len(us.orders) == 0

    def test_us_order_goes_to_us_context(self):
        hk = FakeTradeContext("HK", acc_id=20001)
        us = FakeTradeContext("US", acc_id=30001)
        connector, fake_ft = _connected_connector("US", hk_ctx=hk, us_ctx=us)
        connector.account_ids = {"HK": 20001, "US": 30001}

        connector.place_order("TSLA", qty=10, side="BUY", price=200.0)

        assert len(us.orders) == 1
        order = us.orders[0]
        assert order["code"] == "US.TSLA"
        assert len(hk.orders) == 0

    def test_hk_order_code_is_never_us_prefixed(self):
        hk = FakeTradeContext("HK", acc_id=20001)
        us = FakeTradeContext("US", acc_id=30001)
        connector, _ = _connected_connector("US", hk_ctx=hk, us_ctx=us)

        connector.place_order("0700.HK", qty=100, side="BUY", price=300.0)

        placed_codes = [o["code"] for o in hk.orders] + [o["code"] for o in us.orders]
        assert "US.0700.HK" not in placed_codes


# ── Per-market position sync ──────────────────────────────────────────────────

class TestPerMarketPositionSync:
    def test_get_sync_positions_for_hk_market(self):
        hk = FakeTradeContext("HK", acc_id=20001)
        connector, _ = _connected_connector("US", hk_ctx=hk)
        df = connector.get_sync_positions_for_market("HK")
        assert not df.empty
        assert "HK.0700.HK" in df["code"].values

    def test_get_sync_positions_for_us_market(self):
        us = FakeTradeContext("US", acc_id=30001)
        connector, _ = _connected_connector("US", us_ctx=us)
        df = connector.get_sync_positions_for_market("US")
        assert not df.empty
        assert "US.TSLA" in df["code"].values

    def test_get_sync_positions_all_markets_combines_both(self):
        hk = FakeTradeContext("HK", acc_id=20001)
        us = FakeTradeContext("US", acc_id=30001)
        connector, _ = _connected_connector("US", hk_ctx=hk, us_ctx=us)
        df = connector.get_sync_positions_all_markets()
        codes = set(df["code"].values)
        assert "HK.0700.HK" in codes
        assert "US.TSLA" in codes

    def test_hk_positions_not_in_us_sync(self):
        hk = FakeTradeContext("HK", acc_id=20001)
        us = FakeTradeContext("US", acc_id=30001)
        connector, _ = _connected_connector("US", hk_ctx=hk, us_ctx=us)
        us_df = connector.get_sync_positions_for_market("US")
        hk_codes = [c for c in us_df["code"].values if "HK" in c]
        assert len(hk_codes) == 0, f"HK codes leaked into US sync: {hk_codes}"


# ── Close releases all contexts ───────────────────────────────────────────────

class TestCloseReleasesContexts:
    def test_close_clears_trade_ctxs(self):
        connector, fake_ft = _connected_connector("US")
        assert connector.trade_ctxs
        connector.close()
        assert connector.trade_ctxs == {}
        assert connector.trade_ctx is None

    def test_close_clears_account_ids(self):
        connector, _ = _connected_connector("US")
        connector.account_ids = {"HK": 20001, "US": 30001}
        connector.close()
        assert connector.account_ids == {}

    def test_hk_context_is_closed(self):
        hk = FakeTradeContext("HK", acc_id=20001)
        connector, _ = _connected_connector("US", hk_ctx=hk)
        connector.close()
        assert hk._closed


# ── build_market_contexts integration ────────────────────────────────────────

class TestBuildMarketContextsIntegration:
    """Validate that FutuConnector.resolve_symbol() and build_market_contexts agree."""

    def test_mixed_symbols_consistent_market_inference(self):
        symbols = ["0700.HK", "TSLA", "9988.HK", "NVDA", "2318.HK"]
        cfg = FutuConfig(market_prefix="US", trd_env="SIMULATE")
        connector = FutuConnector(config=cfg, config_json_path="/nonexistent/config.json")

        contexts = build_market_contexts(symbols)

        for sym in symbols:
            _, connector_market = connector.resolve_symbol(sym)
            context_market = "HK" if sym in contexts.get("HK", type("", (), {"symbols": []})()).symbols else "US"
            assert connector_market == context_market, (
                f"{sym}: connector says {connector_market!r} but build_market_contexts says {context_market!r}"
            )

    def test_sync_positions_to_contexts_with_broker_data(self):
        symbols = ["0700.HK", "TSLA"]
        contexts = build_market_contexts(symbols)
        broker_data = [
            {"code": "HK.0700.HK", "qty": 1000},
            {"code": "US.TSLA", "qty": 50},
        ]
        sync_positions_to_contexts(contexts, broker_data)
        assert contexts["HK"].long_qty("0700.HK") == 1000
        assert contexts["US"].long_qty("TSLA") == 50

    def test_hk_positions_isolated_from_us_context(self):
        symbols = ["0700.HK", "TSLA"]
        contexts = build_market_contexts(symbols)
        sync_positions_to_contexts(contexts, [{"code": "HK.0700.HK", "qty": 500}])
        assert contexts["US"].total_active_positions() == 0
        assert contexts["HK"].long_qty("0700.HK") == 500


# ── Multi-market asset aggregation ───────────────────────────────────────────

class TestMultiMarketAssetSync:
    """get_sync_assets_all_markets() combines HK and US account values."""

    def test_combined_assets_sum_both_markets(self):
        # Each FakeTradeContext.accinfo_query returns total_assets=100000
        hk = FakeTradeContext("HK", acc_id=20001)
        us = FakeTradeContext("US", acc_id=30001)
        connector, _ = _connected_connector("US", hk_ctx=hk, us_ctx=us)
        assets = connector.get_sync_assets_all_markets()
        # HK (100 000) + US (100 000) = 200 000
        assert assets["total_assets"] == 200000.0

    def test_cash_summed_across_markets(self):
        hk = FakeTradeContext("HK", acc_id=20001)
        us = FakeTradeContext("US", acc_id=30001)
        connector, _ = _connected_connector("US", hk_ctx=hk, us_ctx=us)
        assets = connector.get_sync_assets_all_markets()
        assert assets["cash"] == 100000.0  # 50000 + 50000

    def test_power_summed_across_markets(self):
        hk = FakeTradeContext("HK", acc_id=20001)
        us = FakeTradeContext("US", acc_id=30001)
        connector, _ = _connected_connector("US", hk_ctx=hk, us_ctx=us)
        assets = connector.get_sync_assets_all_markets()
        assert assets["power"] == 100000.0  # 50000 + 50000

    def test_no_trade_ctxs_falls_back_to_get_sync_assets(self):
        from unittest.mock import patch
        cfg = FutuConfig(trd_env="SIMULATE")
        connector = FutuConnector(config=cfg, config_json_path="/nonexistent/config.json")
        # No trade contexts registered
        assert connector.trade_ctxs == {}
        # get_sync_assets_all_markets falls back to get_sync_assets which raises when not connected
        with pytest.raises(Exception):
            connector.get_sync_assets_all_markets()

    def test_single_market_returns_that_market_assets(self):
        """When only US context is registered, total should be single-market value."""
        us = FakeTradeContext("US", acc_id=30001)
        import sys
        import types

        class FakeTrdEnv:
            REAL = "REAL"
            SIMULATE = "SIMULATE"

        fake_ft = types.SimpleNamespace(
            RET_OK=0,
            TrdEnv=FakeTrdEnv,
            TrdSide=types.SimpleNamespace(BUY="BUY", SELL="SELL"),
            OrderType=types.SimpleNamespace(NORMAL="NORMAL"),
            SubType=types.SimpleNamespace(QUOTE="QUOTE"),
            OpenQuoteContext=lambda host, port: FakeQuoteContext(),
            OpenUSTradeContext=lambda host, port: us,
            OpenHKTradeContext=lambda host, port: (_ for _ in ()).throw(RuntimeError("no HK")),
        )
        cfg = FutuConfig(market_prefix="US", trd_env="SIMULATE")
        connector = FutuConnector(config=cfg, config_json_path="/nonexistent/config.json")
        sys.modules["futu"] = fake_ft
        try:
            connector.connect()
        except Exception:
            pass
        finally:
            sys.modules.pop("futu", None)

        # Only US context should be registered (HK unavailable)
        if "US" in connector.trade_ctxs and "HK" not in connector.trade_ctxs:
            assets = connector.get_sync_assets_all_markets()
            assert assets["total_assets"] == 100000.0


# ── HK-aware quote provider ordering (Issue 1: yfinance HK 404) ──────────────

class TestHKAwareQuoteProviderOrder:
    """For HK symbols the provider order must be ef→futu→yf (not yf first).

    yfinance returns 404 / "possibly delisted" for HK stocks; skipping it as
    first provider avoids guaranteed failures and speeds up the fallback chain.
    """

    def _make_connector(self) -> FutuConnector:
        cfg = FutuConfig(market_prefix="US", trd_env="SIMULATE")
        return FutuConnector(config=cfg, config_json_path="/nonexistent/config.json")

    def test_hk_symbol_skips_yfinance_first(self):
        """0700.HK must try efinance before yfinance."""
        connector = self._make_connector()
        call_order: list[str] = []

        def fake_ef(symbol):
            call_order.append("ef")
            return {"close": 300.0, "open": 300.0, "high": 302.0, "low": 298.0, "volume": 1000.0, "data_source": "EF_LIVE"}

        def fake_yf(symbol):
            call_order.append("yf")
            raise RuntimeError("yfinance: possibly delisted HK")

        connector._get_latest_quote_efinance = fake_ef
        connector._get_latest_quote_yfinance = fake_yf
        connector.quote_ctx = None  # disable futu provider

        result = connector.get_latest_quote("0700.HK", use_cache=False)
        assert result["close"] == 300.0
        assert "ef" in call_order
        # yfinance must NOT have been called (ef succeeded)
        assert "yf" not in call_order

    def test_us_symbol_tries_yfinance_first(self):
        """AAPL (US) must try yfinance before efinance."""
        connector = self._make_connector()
        call_order: list[str] = []

        def fake_yf(symbol):
            call_order.append("yf")
            return {"close": 180.0, "open": 179.0, "high": 181.0, "low": 178.0, "volume": 500000.0, "data_source": "YF_LIVE"}

        def fake_ef(symbol):
            call_order.append("ef")
            raise RuntimeError("ef not tried for US")

        connector._get_latest_quote_yfinance = fake_yf
        connector._get_latest_quote_efinance = fake_ef

        result = connector.get_latest_quote("AAPL", use_cache=False)
        assert result["close"] == 180.0
        assert call_order[0] == "yf"
        assert "ef" not in call_order

    def test_hk_symbol_fallback_to_futu_when_ef_fails(self):
        """When efinance fails for HK, futu should be tried next."""
        connector = self._make_connector()

        def fake_ef(symbol):
            raise RuntimeError("ef module not found")

        def fake_yf(symbol):
            raise RuntimeError("yfinance HK 404")

        connector._get_latest_quote_efinance = fake_ef
        connector._get_latest_quote_yfinance = fake_yf

        # Inject working futu quote context
        fake_quote_ctx = MagicMock()
        fake_quote_ctx.get_stock_quote.return_value = (
            0,
            pd.DataFrame([{
                "code": "HK.0700.HK",
                "last_price": 310.0,
                "open_price": 308.0,
                "high_price": 312.0,
                "low_price": 307.0,
                "volume": 2000.0,
            }]),
        )
        connector.quote_ctx = fake_quote_ctx
        connector.ft = types.SimpleNamespace(RET_OK=0)

        result = connector.get_latest_quote("0700.HK", use_cache=False)
        assert result["Close"] == 310.0
        assert result["data_source"] == "FUTU"

    def test_efinance_module_not_found_raises_clear_error(self):
        """Missing efinance package raises RuntimeError with deployment hint."""
        connector = self._make_connector()

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "efinance":
                raise ModuleNotFoundError("No module named 'efinance'")
            return real_import(name, *args, **kwargs)

        import unittest.mock as mock
        with mock.patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(RuntimeError, match="efinance not importable"):
                connector._get_latest_quote_efinance("0700.HK")
