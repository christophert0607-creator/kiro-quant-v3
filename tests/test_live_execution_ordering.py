"""Acceptance tests for live-execution-state-machine refactor.

Acceptance criteria:
- broker exception: position dict completely unchanged
- auto_trade=False: decision is recorded (not silently discarded)
- paper_trading=True: FutuConnector.place_order() is never called
- BUY and SELL both go through the same dispatch_execution boundary
"""

import sys
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

sys.modules.setdefault("requests", SimpleNamespace(post=lambda *a, **kw: None))
sys.modules.setdefault(
    "v3_pipeline.models.manager",
    SimpleNamespace(
        DataPreparer=lambda **kw: SimpleNamespace(
            lookback=2,
            target_col="Close",
            is_fitted=True,
            feature_columns=None,
            fit_transform=lambda frame: frame,
        ),
        ModelManager=object,
    ),
)

from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop


# ── Minimal stubs ─────────────────────────────────────────────────────────────


class _FakeConnector:
    """Configurable stub for FutuConnector."""

    def __init__(self, place_order_raises=None):
        self._raise = place_order_raises
        self.place_order_calls = []

    class config:
        market_prefix = "US"

    config_market_accounts = {}
    account_ids = {}

    def connect(self): pass
    def close(self): pass
    def heartbeat(self): return True
    def get_sync_assets(self): return {"total_assets": 100_000.0}
    def get_sync_positions(self): return pd.DataFrame(columns=["code", "qty", "can_sell_qty"])
    def get_latest_quote(self, symbol):
        return {"Date": pd.Timestamp("2026-01-01"), "Open": 100.0, "High": 101.0,
                "Low": 99.0, "Close": 100.0, "Volume": 10000, "data_source": "test", "symbol": symbol}

    def get_order_reference_price(self, _sym, _side, fallback_price):
        return fallback_price

    def get_open_orders(self):
        return pd.DataFrame([])

    def place_order(self, symbol, qty, side, price):
        self.place_order_calls.append((symbol, qty, side, price))
        if self._raise:
            raise self._raise

    def validate_trading_ready(self): pass
    def _resolved_trd_env(self): return "SIMULATE"


class _ZeroPaddedHKPositionConnector(_FakeConnector):
    """Broker reports HK.03690 while engine trades 3690.HK."""

    def resolve_symbol(self, symbol):
        # Deliberately mirror the current connector's HK format, which differs
        # from the broker position row returned by FutuOpenD.
        return f"HK.{symbol}", "HK"

    def get_sync_positions_all_markets(self):
        return pd.DataFrame([{"code": "HK.03690", "qty": 100, "can_sell_qty": 100}])


class _FakeModelManager:
    data_preparer = SimpleNamespace(
        lookback=2, target_col="Close", is_fitted=True, feature_columns=None,
        fit_transform=lambda frame: frame,
    )

    def predict(self, *a, **kw): return 0.0
    def predict_pattern(self, *a, **kw): return {"pattern": "Unknown", "confidence": 0.0, "probs": {}}


class _FakeRisk:
    config = SimpleNamespace(max_daily_loss_fraction=1.0)
    def circuit_breaker_triggered(self, *a, **kw): return False
    def allow_trade_with_ror(self, *a, **kw): return True
    def allow_daily_loss(self, *a, **kw): return True
    def allow_trade_with_var_cvar(self, *a, **kw): return True
    def estimate_cvar_95(self, *a, **kw): return -0.05


def _make_loop(auto_trade=True, paper_trading=False, connector=None, **config_overrides) -> LiveTradingLoop:
    cfg = LiveConfig(
        symbol="TSLA",
        symbols_list=["TSLA"],
        prediction_threshold=0.01,
        prediction_thresholds={"TSLA": 0.01},
        auto_trade=auto_trade,
        paper_trading=paper_trading,
        log_trade_decisions=True,
        polling_seconds=1,
        **config_overrides,
    )
    loop = LiveTradingLoop(
        model_manager=_FakeModelManager(),
        risk_controller=_FakeRisk(),
        futu_connector=connector or _FakeConnector(),
        data_manager=None,
        feature_generator=SimpleNamespace(generate=lambda f: f),
        config=cfg,
    )
    loop.alpha_engine = SimpleNamespace(select_features=lambda _sym, f: f)
    return loop


# ── Test: broker exception does NOT mutate position dict ─────────────────────


def test_live_buy_broker_failure_position_unchanged():
    connector = _FakeConnector(place_order_raises=RuntimeError("broker down"))
    loop = _make_loop(auto_trade=True, paper_trading=False, connector=connector)
    loop.position_qty_by_symbol["TSLA"] = 0

    loop._execute("TSLA", "BUY", qty=10, price=100.0, reason="test")

    assert loop.position_qty_by_symbol["TSLA"] == 0, (
        "BUY: broker failure must leave position unchanged"
    )


def test_live_sell_broker_failure_position_unchanged():
    connector = _FakeConnector(place_order_raises=RuntimeError("broker down"))
    loop = _make_loop(auto_trade=True, paper_trading=False, connector=connector)
    loop.position_qty_by_symbol["TSLA"] = 50

    loop._execute("TSLA", "SELL", qty=50, price=100.0, reason="test")

    assert loop.position_qty_by_symbol["TSLA"] == 50, (
        "SELL: broker failure must leave position unchanged"
    )


def test_live_buy_success_mutates_position_after_broker():
    connector = _FakeConnector()
    loop = _make_loop(auto_trade=True, paper_trading=False, connector=connector)
    loop.position_qty_by_symbol["TSLA"] = 0

    with patch.object(loop, "_append_decision_trace"):
        loop._execute("TSLA", "BUY", qty=10, price=100.0, reason="test")

    assert loop.position_qty_by_symbol["TSLA"] == 10
    assert len(connector.place_order_calls) == 1


# ── Test: auto_trade=False records simulated decision ────────────────────────


def test_auto_trade_false_records_decision_not_silent():
    loop = _make_loop(auto_trade=False)
    loop.position_qty_by_symbol["TSLA"] = 0

    decisions = []
    with patch.object(loop, "_append_decision_trace", side_effect=decisions.append):
        loop._execute("TSLA", "BUY", qty=10, price=100.0, reason="test_simulate")

    assert len(decisions) == 1, "SIMULATE mode must call _append_decision_trace once"
    assert decisions[0]["mode"] == "simulate"
    assert loop.position_qty_by_symbol["TSLA"] == 0, "SIMULATE must not mutate position"


def test_auto_trade_false_sell_records_decision_not_silent():
    loop = _make_loop(auto_trade=False)
    loop.position_qty_by_symbol["TSLA"] = 20

    decisions = []
    with patch.object(loop, "_append_decision_trace", side_effect=decisions.append):
        loop._execute("TSLA", "SELL", qty=20, price=100.0, reason="test_simulate")

    assert len(decisions) == 1
    assert decisions[0]["mode"] == "simulate"
    assert loop.position_qty_by_symbol["TSLA"] == 20, "SIMULATE SELL must not mutate position"


# ── Test: paper_trading=True never calls place_order ─────────────────────────


def test_paper_trading_buy_does_not_call_place_order():
    connector = _FakeConnector()
    loop = _make_loop(auto_trade=True, paper_trading=True, connector=connector)
    loop.position_qty_by_symbol["TSLA"] = 0

    loop._execute("TSLA", "BUY", qty=10, price=100.0, reason="test_paper")

    assert connector.place_order_calls == [], "PAPER mode must never call place_order()"
    assert loop.position_qty_by_symbol["TSLA"] == 10, "PAPER BUY must still mutate position locally"


def test_paper_trading_sell_does_not_call_place_order():
    connector = _FakeConnector()
    loop = _make_loop(auto_trade=True, paper_trading=True, connector=connector)
    loop.position_qty_by_symbol["TSLA"] = 30

    loop._execute("TSLA", "SELL", qty=30, price=100.0, reason="test_paper")

    assert connector.place_order_calls == [], "PAPER mode must never call place_order()"
    assert loop.position_qty_by_symbol["TSLA"] == 0


def test_live_sell_precheck_matches_zero_padded_hk_broker_position():
    connector = _ZeroPaddedHKPositionConnector()
    loop = _make_loop(auto_trade=True, paper_trading=False, connector=connector)
    loop.position_qty_by_symbol["3690.HK"] = 100

    loop._execute("3690.HK", "SELL", qty=100, price=83.0, reason="model_signal")

    assert connector.place_order_calls == [("3690.HK", 100, "SELL", 83.0)]
    assert loop.position_qty_by_symbol["3690.HK"] == 0


def test_live_order_rate_limit_caps_orders_per_cycle():
    connector = _FakeConnector()
    loop = _make_loop(
        auto_trade=True,
        paper_trading=False,
        connector=connector,
        max_orders_per_cycle=1,
        order_throttle_seconds=0,
    )
    loop.position_qty_by_symbol["TSLA"] = 0

    loop._execute("TSLA", "BUY", qty=10, price=100.0, reason="first")
    loop._execute("TSLA", "BUY", qty=10, price=101.0, reason="second")

    assert connector.place_order_calls == [("TSLA", 10, "BUY", 100.0)]
    assert loop.position_qty_by_symbol["TSLA"] == 10


def test_live_order_rate_limit_throttles_within_30_seconds(monkeypatch):
    connector = _FakeConnector()
    loop = _make_loop(
        auto_trade=True,
        paper_trading=False,
        connector=connector,
        max_orders_per_cycle=10,
        order_throttle_seconds=30,
    )
    loop.position_qty_by_symbol["TSLA"] = 0

    now = iter([1_000.0, 1_010.0, 1_031.0])
    monkeypatch.setattr("v3_pipeline.core.main_loop.time.monotonic", lambda: next(now))

    loop._execute("TSLA", "BUY", qty=10, price=100.0, reason="first")
    loop._execute("TSLA", "BUY", qty=10, price=101.0, reason="throttled")
    loop._execute("TSLA", "BUY", qty=10, price=102.0, reason="after_window")

    assert connector.place_order_calls == [
        ("TSLA", 10, "BUY", 100.0),
        ("TSLA", 10, "BUY", 102.0),
    ]
    assert loop.position_qty_by_symbol["TSLA"] == 20


# ── Test: broker is called BEFORE position is mutated (ordering) ─────────────


def test_live_broker_called_before_position_mutated():
    """Verify broker.place_order() is called while position is still at 0."""
    position_at_call_time = {}

    def tracking_place_order(sym, qty, side, price):
        position_at_call_time["before"] = loop.position_qty_by_symbol.get("TSLA", 0)

    connector = _FakeConnector()
    connector.place_order = tracking_place_order
    loop = _make_loop(auto_trade=True, paper_trading=False, connector=connector)
    loop.position_qty_by_symbol["TSLA"] = 0

    with patch.object(loop, "_append_decision_trace"):
        loop._execute("TSLA", "BUY", qty=10, price=100.0, reason="ordering_test")

    assert position_at_call_time["before"] == 0, (
        "Position must still be 0 when place_order() is called (broker first, mutate after)"
    )
    assert loop.position_qty_by_symbol["TSLA"] == 10, "Position must be updated after broker success"
