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
