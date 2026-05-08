"""Tests for circuit breaker false-trigger guard when broker returns zero equity.

Scenario: In SIMULATE/paper mode the Futu broker sometimes returns total_assets=0
(unfunded SIM account or transient connectivity issue).  Before the fix this caused
account_value to be set to 0, producing a 100% drawdown vs the initial 100 000
equity_peak and firing the circuit breaker on every _execute() call.

Fix in _sync_broker_state(): only update account_value when broker returns a
positive value; otherwise preserve the last known value and emit a structured log.
"""
import json
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pandas as pd
import pytest


# ── stubs required before importing main_loop ───────────────────────────────
@dataclass
class _StubPreparer:
    lookback: int = 2
    target_col: str = "Close"
    is_fitted: bool = True
    feature_columns: list = None

    def fit_transform(self, frame):
        self.is_fitted = True
        self.feature_columns = [c for c in frame.columns if c != "Date"]
        return frame


sys.modules.setdefault("requests", SimpleNamespace(post=lambda *a, **k: None))
sys.modules.setdefault(
    "v3_pipeline.models.manager",
    SimpleNamespace(
        DataPreparer=lambda **kwargs: _StubPreparer(**kwargs),
        ModelManager=object,
    ),
)

from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop  # noqa: E402
from v3_pipeline.risk.manager import RiskConfig, RiskController  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

class _ZeroAssetConnector:
    """Broker that always returns total_assets=0 (SIM account unfunded)."""

    class config:
        market_prefix = "US"

    def connect(self):
        return None

    def close(self):
        return None

    def heartbeat(self):
        return True

    def get_sync_assets(self):
        return {"total_assets": 0.0}

    def get_sync_positions(self):
        return pd.DataFrame(columns=["code", "qty", "can_sell_qty"])

    def get_latest_quote(self, symbol):
        return {
            "Date": pd.Timestamp("2026-05-08T10:00:00Z"),
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.0,
            "Volume": 10_000,
            "data_source": "test",
            "symbol": symbol,
        }

    def get_order_reference_price(self, _symbol, _side, fallback_price):
        return fallback_price

    def get_open_orders(self):
        return pd.DataFrame([])


class _PositiveAssetConnector(_ZeroAssetConnector):
    """Broker that returns a meaningful total_assets value."""

    def get_sync_assets(self):
        return {"total_assets": 200_000.0}


def _make_loop(connector=None, symbols=None):
    symbols = symbols or ["AAPL"]
    cfg = LiveConfig(
        symbol=symbols[0],
        symbols_list=symbols,
        prediction_threshold=0.01,
        prediction_thresholds={s: 0.01 for s in symbols},
        auto_trade=True,
        paper_trading=False,
        log_trade_decisions=False,
        polling_seconds=1,
    )
    loop = LiveTradingLoop(
        model_manager=SimpleNamespace(
            data_preparer=_StubPreparer(),
            predict=lambda *a, **k: 100.0,
        ),
        risk_controller=RiskController(config=RiskConfig(max_drawdown=0.20)),
        futu_connector=connector or _ZeroAssetConnector(),
        data_manager=None,
        feature_generator=SimpleNamespace(generate=lambda f: f),
        config=cfg,
    )
    return loop


# ── tests ────────────────────────────────────────────────────────────────────

class TestBrokerSyncZeroEquityGuard:
    def test_account_value_not_zeroed_when_broker_returns_zero(self):
        """account_value must stay at its last known value when broker returns 0."""
        loop = _make_loop(connector=_ZeroAssetConnector())
        loop.account_value = 100_000.0

        loop._sync_broker_state()

        assert loop.account_value == 100_000.0, (
            "account_value was overwritten to 0; circuit breaker would fire on every trade"
        )

    def test_account_value_updated_when_broker_returns_positive(self):
        """account_value must be updated when broker returns a positive value."""
        loop = _make_loop(connector=_PositiveAssetConnector())
        loop.account_value = 100_000.0

        loop._sync_broker_state()

        assert loop.account_value == 200_000.0

    def test_circuit_breaker_does_not_fire_after_zero_equity_sync(self):
        """With the fix, a zero-equity broker response must NOT trigger circuit breaker."""
        loop = _make_loop(connector=_ZeroAssetConnector())
        loop.account_value = 100_000.0
        loop.equity_peak = 100_000.0

        loop._sync_broker_state()

        fired = loop.risk_controller.circuit_breaker_triggered(
            loop.equity_peak, loop.account_value
        )
        assert not fired, (
            "Circuit breaker fired after zero-equity broker sync; "
            "account_value should have been preserved"
        )

    def test_zero_equity_emits_structured_log_event(self, capsys):
        """broker_sync_zero_equity structured event must be emitted when broker returns 0."""
        loop = _make_loop(connector=_ZeroAssetConnector())
        loop.account_value = 50_000.0

        events = []
        original_emit = loop._emit_structured

        def _capture_emit(event_type, **fields):
            events.append({"event": event_type, **fields})
            original_emit(event_type, **fields)

        loop._emit_structured = _capture_emit
        loop._sync_broker_state()

        assert any(e["event"] == "broker_sync_zero_equity" for e in events), (
            "Expected broker_sync_zero_equity structured event not emitted"
        )
        evt = next(e for e in events if e["event"] == "broker_sync_zero_equity")
        assert evt["kept_value"] == 50_000.0

    def test_account_value_preserved_across_multiple_zero_syncs(self):
        """Multiple consecutive zero-equity syncs must not drift account_value."""
        loop = _make_loop(connector=_ZeroAssetConnector())
        loop.account_value = 75_000.0

        for _ in range(5):
            loop._sync_broker_state()

        assert loop.account_value == 75_000.0

    def test_positive_equity_after_zero_resets_correctly(self):
        """After zero-equity phase, a positive broker response must update account_value."""
        class _FlippingConnector(_ZeroAssetConnector):
            def __init__(self):
                self._call = 0

            def get_sync_assets(self):
                self._call += 1
                return {"total_assets": 0.0 if self._call <= 2 else 120_000.0}

        loop = _make_loop(connector=_FlippingConnector())
        loop.account_value = 100_000.0

        loop._sync_broker_state()  # call 1 — zero, preserve 100k
        assert loop.account_value == 100_000.0

        loop._sync_broker_state()  # call 2 — zero, preserve 100k
        assert loop.account_value == 100_000.0

        loop._sync_broker_state()  # call 3 — positive 120k, update
        assert loop.account_value == 120_000.0


class TestCircuitBreakerStandaloneLogic:
    """Unit tests for RiskController.circuit_breaker_triggered."""

    def test_no_trigger_when_equity_peak_zero(self):
        ctrl = RiskController(config=RiskConfig(max_drawdown=0.20))
        assert not ctrl.circuit_breaker_triggered(0.0, 0.0)

    def test_trigger_on_large_drawdown(self):
        ctrl = RiskController(config=RiskConfig(max_drawdown=0.20))
        # 100k peak, 70k current = 30% drawdown > 20% limit
        assert ctrl.circuit_breaker_triggered(100_000.0, 70_000.0)

    def test_no_trigger_within_limit(self):
        ctrl = RiskController(config=RiskConfig(max_drawdown=0.20))
        # 100k peak, 85k current = 15% drawdown < 20% limit
        assert not ctrl.circuit_breaker_triggered(100_000.0, 85_000.0)

    def test_no_trigger_at_exact_limit(self):
        ctrl = RiskController(config=RiskConfig(max_drawdown=0.20))
        # 100k peak, 80k current = exactly 20% — boundary: triggered only when >=
        assert ctrl.circuit_breaker_triggered(100_000.0, 80_000.0)

    def test_simulate_zero_equity_false_positive_scenario(self):
        """Before fix: zero broker equity with 100k peak would trigger circuit breaker."""
        ctrl = RiskController(config=RiskConfig(max_drawdown=0.20))
        # This is the scenario that happens WITHOUT the fix
        equity_peak = 100_000.0  # set from initial account_value
        current_equity = 0.0     # broker returned 0
        # Without the fix, this fires — drawdown = 100%
        assert ctrl.circuit_breaker_triggered(equity_peak, current_equity)
        # The fix prevents current_equity from being 0 in the first place
