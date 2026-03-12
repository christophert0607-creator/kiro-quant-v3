import types

import pandas as pd
import pytest

from v3_pipeline.core.futu_connector import FutuConfig, FutuConnector


class DummyTradeContext:
    def __init__(self):
        self.unlock_calls = []
        self._acc_df = pd.DataFrame([{"acc_id": 123456}])

    def unlock_trade(self, password):
        self.unlock_calls.append(password)
        return 0, "ok"

    def get_acc_list(self, **kwargs):
        return 0, self._acc_df


class DummyQuoteContext:
    pass


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


def test_unlock_trading_requires_password_in_real_mode():
    cfg = FutuConfig(trd_env="REAL", trade_password="")
    connector = FutuConnector(config=cfg)
    connector.ft = DummyFT
    connector.trade_ctx = DummyTradeContext()

    with pytest.raises(RuntimeError, match="FUTU_TRADE_PASSWORD"):
        connector.unlock_trading("")


def test_connect_auto_unlocks_in_real_mode(monkeypatch):
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


def test_discover_accounts_fallback_without_trd_env_support():
    cfg = FutuConfig(trd_env="REAL", trade_password="secret")
    connector = FutuConnector(config=cfg)
    connector.ft = DummyFT

    class LegacyTradeCtx(DummyTradeContext):
        def get_acc_list(self, **kwargs):
            return 0, self._acc_df

    connector.trade_ctx = LegacyTradeCtx()

    def fail_safe_trade_call(name, **kwargs):
        raise TypeError("get_acc_list() got an unexpected keyword argument 'trd_env'")

    connector._safe_trade_call = fail_safe_trade_call  # type: ignore[method-assign]

    accounts = connector.discover_accounts()

    assert not accounts.empty
    assert int(accounts.iloc[0]["acc_id"]) == 123456


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
