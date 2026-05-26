import types

import pandas as pd
import pytest

from v3_pipeline.core.futu_connector import FutuConfig, FutuConnector


class DummyTradeContext:
    def __init__(self):
        self.unlock_calls = []
        self.place_order_calls = []
        self._acc_df = pd.DataFrame([{"acc_id": 123456}])

    def unlock_trade(self, password):
        self.unlock_calls.append(password)
        return 0, "ok"

    def get_acc_list(self, **kwargs):
        return 0, self._acc_df

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        return 0, pd.DataFrame([{"order_id": "T123"}])

    def accinfo_query(self, **kwargs):
        return 0, pd.DataFrame([{
            "cash": 5000.0,
            "available_funds": "N/A",
            "power": "N/A",
            "max_buying_power": "N/A",
        }])


class DummyQuoteContext:
    def __init__(self):
        self.subscribe_calls = []

    def subscribe(self, codes, sub_types, subscribe_push=False):
        self.subscribe_calls.append((codes, sub_types, subscribe_push))
        return 0, "ok"


class DummyFT:
    RET_OK = 0

    class TrdEnv:
        REAL = "REAL"
        SIMULATE = "SIMULATE"

    class TrdSide:
        BUY = "BUY"
        SELL = "SELL"

    class OrderType:
        NORMAL = "NORMAL"

    @staticmethod
    def OpenQuoteContext(host, port):
        return DummyQuoteContext()

    @staticmethod
    def OpenUSTradeContext(host, port):
        return DummyTradeContext()


def test_unlock_trading_requires_password_in_real_mode(monkeypatch):
    monkeypatch.setenv("FUTU_TRD_ENV", "REAL")
    cfg = FutuConfig(trd_env="REAL", trade_password="")
    connector = FutuConnector(config=cfg)
    connector.ft = DummyFT
    connector.trade_ctx = DummyTradeContext()

    with pytest.raises(RuntimeError, match="FUTU_TRADE_PASSWORD"):
        connector.unlock_trading("")


def test_connect_auto_unlocks_in_real_mode(monkeypatch):
    monkeypatch.setenv("FUTU_TRD_ENV", "REAL")
    cfg = FutuConfig(trd_env="REAL", trade_password="secret")
    connector = FutuConnector(config=cfg)

    fake_module = types.SimpleNamespace(
        RET_OK=DummyFT.RET_OK,
        TrdEnv=DummyFT.TrdEnv,
        TrdSide=DummyFT.TrdSide,
        OrderType=DummyFT.OrderType,
        OpenQuoteContext=DummyFT.OpenQuoteContext,
        OpenUSTradeContext=DummyFT.OpenUSTradeContext,
    )
    monkeypatch.setitem(__import__("sys").modules, "futu", fake_module)

    connector.connect()

    assert connector.trade_ctx is not None
    assert connector.trade_ctx.unlock_calls == ["secret"]


def test_discover_accounts_uses_bare_get_acc_list():
    """get_acc_list() takes no kwargs in any current SDK version. discover_accounts
    must call it without trd_env or acc_id, otherwise the call raises TypeError and
    discovered_accounts comes back empty (production bug, observed 2026-05-17)."""
    cfg = FutuConfig(trd_env="REAL", trade_password="secret")
    connector = FutuConnector(config=cfg)
    connector.ft = DummyFT

    seen_kwargs: list[dict] = []

    class StrictTradeCtx:
        _acc_df = pd.DataFrame([{"acc_id": 123456}])

        def get_acc_list(self, **kwargs):
            seen_kwargs.append(dict(kwargs))
            # Real Futu SDK rejects ANY kwargs here
            if kwargs:
                raise TypeError(
                    f"get_acc_list() got an unexpected keyword argument {next(iter(kwargs))!r}"
                )
            return 0, self._acc_df

    connector.trade_ctx = StrictTradeCtx()

    accounts = connector.discover_accounts()

    assert seen_kwargs == [{}], f"discover_accounts must call get_acc_list() with no kwargs; got {seen_kwargs}"
    assert not accounts.empty
    assert int(accounts.iloc[0]["acc_id"]) == 123456


def test_discover_accounts_returns_empty_on_total_failure():
    """If get_acc_list itself raises (network, unauth, etc.), discover_accounts swallows
    the error and returns an empty frame instead of crashing the launcher."""
    cfg = FutuConfig(trd_env="SIMULATE", trade_password="secret")
    connector = FutuConnector(config=cfg)
    connector.ft = DummyFT

    class DeadCtx:
        def get_acc_list(self):
            raise RuntimeError("trade ctx not connected")

    connector.trade_ctx = DeadCtx()

    accounts = connector.discover_accounts()
    assert accounts.empty


def test_discover_accounts_skips_when_not_connected():
    cfg = FutuConfig(trd_env="SIMULATE")
    connector = FutuConnector(config=cfg)
    connector.ft = DummyFT
    connector.trade_ctx = None

    accounts = connector.discover_accounts()
    assert accounts.empty


def test_connect_does_not_unlock_in_simulate_mode(monkeypatch):
    cfg = FutuConfig(trd_env="SIMULATE", trade_password="secret")
    connector = FutuConnector(config=cfg)

    fake_module = types.SimpleNamespace(
        RET_OK=DummyFT.RET_OK,
        TrdEnv=DummyFT.TrdEnv,
        TrdSide=DummyFT.TrdSide,
        OrderType=DummyFT.OrderType,
        OpenQuoteContext=DummyFT.OpenQuoteContext,
        OpenUSTradeContext=DummyFT.OpenUSTradeContext,
    )
    monkeypatch.setitem(__import__("sys").modules, "futu", fake_module)

    connector.connect()

    assert connector.trade_ctx is not None
    assert connector.trade_ctx.unlock_calls == []


def test_place_order_reprices_na_string_before_broker_submission(monkeypatch):
    """Broker/live paths sometimes pass Yahoo/Futu sentinel prices like 'N/A'.
    The connector should sanitize/reprice before calling Futu, not surface a raw
    ValueError as BROKER_REJECTED."""
    cfg = FutuConfig(trd_env="SIMULATE", target_acc_id=123456)
    connector = FutuConnector(config=cfg)
    connector.ft = DummyFT
    connector.trade_ctx = DummyTradeContext()
    connector.trade_ctxs = {"US": connector.trade_ctx}
    monkeypatch.setattr(connector, "validate_trading_ready", lambda **kwargs: None)
    monkeypatch.setattr(connector, "get_order_reference_price", lambda *args, **kwargs: 123.456)

    connector.place_order("XOM", 10, "BUY", price="N/A")

    assert connector.trade_ctx.place_order_calls
    sent = connector.trade_ctx.place_order_calls[0]
    assert sent["price"] == 123.46
    assert sent["code"] == "US.XOM"


def test_place_order_rejects_na_string_when_no_valid_reference(monkeypatch):
    cfg = FutuConfig(trd_env="SIMULATE", target_acc_id=123456)
    connector = FutuConnector(config=cfg)
    connector.ft = DummyFT
    connector.trade_ctx = DummyTradeContext()
    connector.trade_ctxs = {"US": connector.trade_ctx}
    monkeypatch.setattr(connector, "validate_trading_ready", lambda **kwargs: None)
    monkeypatch.setattr(connector, "get_order_reference_price", lambda *args, **kwargs: 0.0)

    with pytest.raises(RuntimeError, match="invalid_order_price"):
        connector.place_order("XOM", 10, "BUY", price="N/A")

    assert connector.trade_ctx.place_order_calls == []


def test_validate_trading_ready_ignores_na_account_metrics_when_cash_is_valid(monkeypatch):
    cfg = FutuConfig(trd_env="SIMULATE", target_acc_id=123456)
    connector = FutuConnector(config=cfg)
    connector.ft = DummyFT
    connector.trade_ctx = DummyTradeContext()

    connector.validate_trading_ready(symbol="XOM", qty=10, side="BUY", est_price=100.0)


def test_validate_trading_ready_uses_per_market_acc_id_and_never_crosses_hk_to_us():
    cfg = FutuConfig(trd_env="SIMULATE")
    connector = FutuConnector(config=cfg)
    connector.ft = DummyFT
    hk_ctx = DummyTradeContext()
    us_ctx = DummyTradeContext()
    hk_calls = []
    us_calls = []

    def hk_accinfo_query(**kwargs):
        hk_calls.append(kwargs)
        return 0, pd.DataFrame([{"cash": 50000.0}])

    def us_accinfo_query(**kwargs):
        us_calls.append(kwargs)
        return 0, pd.DataFrame([{"cash": 50000.0}])

    hk_ctx.accinfo_query = hk_accinfo_query
    us_ctx.accinfo_query = us_accinfo_query
    connector.trade_ctx = us_ctx
    connector.trade_ctxs = {"HK": hk_ctx, "US": us_ctx}
    connector.config_market_accounts = {"HK": 14239754, "US": 18526451}

    connector.validate_trading_ready(symbol="0700.HK", qty=100, side="BUY", est_price=300.0)
    connector.validate_trading_ready(symbol="XOM", qty=10, side="BUY", est_price=100.0)

    assert hk_calls == [{"acc_id": 14239754, "trd_env": "SIMULATE"}]
    assert us_calls == [{"acc_id": 18526451, "trd_env": "SIMULATE"}]
    assert all(call.get("acc_id") != 18526451 for call in hk_calls)


def test_get_order_reference_price_ignores_na_order_book_and_uses_latest_quote():
    cfg = FutuConfig(trd_env="SIMULATE", target_acc_id=123456)
    connector = FutuConnector(config=cfg)
    connector.ft = DummyFT

    class QuoteCtx:
        def get_order_book(self, code, num=1):
            return 0, {"Ask": [("N/A", 10)], "Bid": [("N/A", 10)]}

    connector.quote_ctx = QuoteCtx()
    connector.get_latest_quote = lambda symbol, use_cache=True: {"Close": "101.23"}

    assert connector.get_order_reference_price("XOM", "BUY", fallback_price="N/A") == 101.23


def test_resolve_symbol_formats_hk_codes_for_futu():
    connector = FutuConnector(config=FutuConfig(market_prefix="US"))

    assert connector.resolve_symbol("0700.HK") == ("HK.00700", "HK")
    assert connector.resolve_symbol("700.HK") == ("HK.00700", "HK")
    assert connector.resolve_symbol("HK.700") == ("HK.00700", "HK")
    assert connector.resolve_symbol("AAPL") == ("US.AAPL", "US")


def test_subscribe_symbols_uses_futu_hk_format():
    connector = FutuConnector(config=FutuConfig(market_prefix="US"))
    connector.ft = types.SimpleNamespace(RET_OK=0, SubType=types.SimpleNamespace(QUOTE="QUOTE"))
    connector.quote_ctx = DummyQuoteContext()

    connector.subscribe_symbols(["0700.HK", "AAPL"])

    assert connector.quote_ctx.subscribe_calls == [(["HK.00700", "US.AAPL"], ["QUOTE"], True)]


def test_connect_respects_no_futu_quote(monkeypatch):
    monkeypatch.setenv("NO_FUTU_QUOTE", "1")
    cfg = FutuConfig(trd_env="SIMULATE", trade_password="secret")
    connector = FutuConnector(config=cfg)

    def forbidden_quote_context(host, port):
        raise AssertionError("OpenQuoteContext should not be called when NO_FUTU_QUOTE=1")

    fake_module = types.SimpleNamespace(
        RET_OK=DummyFT.RET_OK,
        TrdEnv=DummyFT.TrdEnv,
        TrdSide=DummyFT.TrdSide,
        OrderType=DummyFT.OrderType,
        OpenQuoteContext=forbidden_quote_context,
        OpenUSTradeContext=DummyFT.OpenUSTradeContext,
    )
    monkeypatch.setitem(__import__("sys").modules, "futu", fake_module)

    connector.connect()

    assert connector.quote_ctx is None
    assert connector.is_connected


def test_connect_error_in_wsl2_includes_network_hints(monkeypatch):
    cfg = FutuConfig(host="127.0.0.1", port=11111)
    connector = FutuConnector(config=cfg)

    class FailingFT:
        RET_OK = 0

        class TrdEnv:
            REAL = "REAL"
            SIMULATE = "SIMULATE"

        class TrdSide:
            BUY = "BUY"
            SELL = "SELL"

        class OrderType:
            NORMAL = "NORMAL"

        @staticmethod
        def OpenQuoteContext(host, port):
            raise ConnectionError("dial tcp 127.0.0.1:11111: connect: connection refused")

        @staticmethod
        def OpenUSTradeContext(host, port):
            return DummyTradeContext()

    monkeypatch.setattr(FutuConnector, "_is_wsl2", lambda self: True)
    fake_module = types.SimpleNamespace(
        RET_OK=FailingFT.RET_OK,
        TrdEnv=FailingFT.TrdEnv,
        TrdSide=FailingFT.TrdSide,
        OrderType=FailingFT.OrderType,
        OpenQuoteContext=FailingFT.OpenQuoteContext,
        OpenUSTradeContext=FailingFT.OpenUSTradeContext,
    )
    monkeypatch.setitem(__import__("sys").modules, "futu", fake_module)

    with pytest.raises(RuntimeError, match="WSL2 network hint") as exc_info:
        connector.connect()

    msg = str(exc_info.value)
    assert "netsh interface portproxy add v4tov4" in msg
    assert "Configure FutuOpenD to listen on 0.0.0.0" in msg
    assert "FUTU_OPEND_HOST" in msg
