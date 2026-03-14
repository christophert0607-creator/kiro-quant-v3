from pathlib import Path

import pandas as pd

from v3_pipeline.core.futu_connector import FutuConfig, FutuConnector
from v3_pipeline.core.trade_simulation import PaperTradingSimulator, PnLTracker


class _DummyFT:
    RET_OK = 0

    class TrdEnv:
        SIMULATE = "SIMULATE"


class _OrderQueryTradeCtx:
    def __init__(self, statuses):
        self.statuses = list(statuses)

    def order_list_query(self, trd_env=None, order_id=None, **kwargs):
        status = self.statuses.pop(0)
        return 0, pd.DataFrame([
            {"order_id": order_id, "order_status": status, "dealt_avg_price": 101.5, "dealt_qty": 3}
        ])


def test_wait_for_fill_returns_status():
    connector = FutuConnector(config=FutuConfig(trd_env="SIMULATE"))
    connector.ft = _DummyFT
    connector.trade_ctx = _OrderQueryTradeCtx(["SUBMITTED", "FILLED_ALL"])

    result = connector.wait_for_fill("abc", timeout_seconds=2.0, poll_interval_seconds=0.01)
    assert result["order_id"] == "abc"
    assert result["dealt_qty"] == 3
    assert result["filled_avg_price"] == 101.5


def test_paper_trading_and_pnl_report(tmp_path: Path):
    pnl_path = tmp_path / "paper_trading_pnl.json"
    report_path = tmp_path / "pnl_report.json"

    tracker = PnLTracker(out_path=report_path)
    sim = PaperTradingSimulator(starting_cash=10000.0, pnl_path=pnl_path, tracker=tracker)

    sim.execute_order("TSLA", "BUY", qty=10, reference_price=100.0)
    sim.execute_order("TSLA", "SELL", qty=4, reference_price=110.0)
    out = sim.mark_to_market({"TSLA": 120.0})

    assert out["cash"] > 0
    assert out["positions"]["TSLA"]["qty"] == 6
    assert (tmp_path / "paper_trading_pnl.json").exists()
    assert (tmp_path / "pnl_report.json").exists()


def test_reconnect_backoff_caps_attempts_and_uses_bounded_delays(monkeypatch):
    connector = FutuConnector(config=FutuConfig(trd_env="SIMULATE"))
    sleeps = []
    attempts = {"n": 0}

    def fake_connect():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("boom")

    monkeypatch.setattr(connector, "connect", fake_connect)
    monkeypatch.setattr(connector, "_safe_close_contexts", lambda: None)
    monkeypatch.setattr("v3_pipeline.core.futu_connector.time.sleep", lambda seconds: sleeps.append(seconds))

    connector.reconnect(max_retries=10, base_delay_seconds=5)

    assert attempts["n"] == 3
    assert sleeps == [2, 4]


def test_reconnect_budget_exceeded_raises_deterministic_error(monkeypatch):
    connector = FutuConnector(config=FutuConfig(trd_env="SIMULATE"))
    attempts = {"n": 0}

    def fake_connect():
        attempts["n"] += 1
        raise RuntimeError("boom")

    clock = {"now": 0.0}

    def fake_time():
        clock["now"] += 11.0
        return clock["now"]

    monkeypatch.setattr(connector, "connect", fake_connect)
    monkeypatch.setattr(connector, "_safe_close_contexts", lambda: None)
    monkeypatch.setattr("v3_pipeline.core.futu_connector.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("v3_pipeline.core.futu_connector.time.time", fake_time)

    try:
        connector.reconnect(max_retries=10, base_delay_seconds=5)
        raise AssertionError("expected reconnect to fail")
    except RuntimeError as exc:
        assert "RECONNECT_BUDGET_EXCEEDED" in str(exc)

    assert 1 <= attempts["n"] <= 3
