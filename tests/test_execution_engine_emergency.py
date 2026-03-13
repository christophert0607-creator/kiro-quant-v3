import pandas as pd

import execution_engine as ee


class _DummyFT:
    RET_OK = 0

    class TrdEnv:
        SIMULATE = "SIM"
        REAL = "REAL"

    class TrdSide:
        SELL = "SELL"

    class OrderType:
        MARKET = "MARKET"


class _TradeCtx:
    def __init__(self):
        self.calls = []

    def place_order(self, **kwargs):
        self.calls.append(kwargs)
        return 0, pd.DataFrame([{"order_id": ["OID-1"][0]}])


def test_emergency_liquidate_all_sells_positions(monkeypatch):
    engine = ee.ExecutionEngine()
    engine.ft = _DummyFT
    monkeypatch.setattr(ee, "ft", _DummyFT)
    engine.trade_ctx = _TradeCtx()
    monkeypatch.setattr(engine, "get_positions", lambda: {"US.TSLA": {"qty": 5}, "US.NVDA": {"qty": 2}})

    result = engine.emergency_liquidate_all()

    assert result["status"] == "OK"
    assert len(result["orders"]) == 2
    assert all(c["trd_side"] == _DummyFT.TrdSide.SELL for c in engine.trade_ctx.calls)


def test_check_emergency_triggers_liquidates_on_daily_loss(monkeypatch):
    engine = ee.ExecutionEngine()
    monkeypatch.setattr(engine, "get_account_info", lambda: {"total_assets": 1000})
    monkeypatch.setattr(ee.ss, "get_daily_pnl", lambda state: -100)
    monkeypatch.setattr(ee.ss, "set_circuit_break", lambda state, broken: state.update({"circuit_broken": broken}))
    monkeypatch.setattr(ee.ss, "save", lambda state: None)
    monkeypatch.setattr(engine, "emergency_liquidate_all", lambda: {"status": "OK", "orders": []})

    out = engine.check_emergency_triggers(state={})

    assert out["triggered"] is True
    assert out["reason"] == "MaxDailyLossEmergency"


def test_execute_signal_success_log_contains_stock_code(monkeypatch, caplog):
    engine = ee.ExecutionEngine()

    monkeypatch.setattr(engine, "get_account_info", lambda: {"cash": 100000, "total_assets": 100000, "power": 100000})
    monkeypatch.setattr(engine, "get_positions", lambda: {})
    monkeypatch.setattr(
        engine.risk,
        "check",
        lambda **kwargs: {"ok": True, "qty": 1},
    )
    monkeypatch.setattr(engine, "_place_with_retry", lambda code, signal, qty, price: {"status": "OK", "order_id": "OID-LOG-1"})
    monkeypatch.setattr(ee.ss, "load", lambda: {})
    monkeypatch.setattr(ee.ss, "record_order", lambda state, code, signal, qty, price: None)
    monkeypatch.setattr(ee.ss, "save", lambda state: None)

    with caplog.at_level("INFO", logger="ExecutionEngine"):
        monkeypatch.setattr(engine, "close_short_if_needed", lambda code, positions: True)
        result = engine.execute("US.TSLA", "BUY", 250.0)

    assert result["status"] == "OK"
    assert "US.TSLA" in caplog.text
    assert "BUY" in caplog.text
