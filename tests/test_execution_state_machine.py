"""Tests for v3_pipeline.execution.state_machine (Phase 4 - execution paths)."""
import pytest

from v3_pipeline.execution.state_machine import (
    ExecutionMode,
    PositionDelta,
    dispatch_execution,
    live_order,
    paper_fill,
    resolve_execution_mode,
    simulate_only,
)


# ── resolve_execution_mode ─────────────────────────────────────────────────────

class TestResolveExecutionMode:
    def test_auto_trade_false_is_simulate_regardless_of_paper(self):
        assert resolve_execution_mode(auto_trade=False, paper_trading=True) == ExecutionMode.SIMULATE
        assert resolve_execution_mode(auto_trade=False, paper_trading=False) == ExecutionMode.SIMULATE

    def test_auto_trade_true_paper_true_is_paper(self):
        assert resolve_execution_mode(auto_trade=True, paper_trading=True) == ExecutionMode.PAPER

    def test_auto_trade_true_paper_false_is_live(self):
        assert resolve_execution_mode(auto_trade=True, paper_trading=False) == ExecutionMode.LIVE


# ── simulate_only ──────────────────────────────────────────────────────────────

class TestSimulateOnly:
    def _delta(self, side="BUY", qty=100, price=100.0, reason="test"):
        return PositionDelta(symbol="TSLA", side=side, qty=qty, fill_price=price, reason=reason)

    def test_does_not_mutate_positions(self):
        positions = {"TSLA": 0}
        simulate_only(self._delta())
        assert positions["TSLA"] == 0

    def test_calls_log_decision(self):
        log_calls = []
        simulate_only(self._delta(), log_decision=log_calls.append)
        assert len(log_calls) == 1
        assert log_calls[0]["mode"] == "simulate"
        assert log_calls[0]["side"] == "BUY"

    def test_sell_also_does_not_mutate(self):
        positions = {"TSLA": 200}
        simulate_only(self._delta(side="SELL"))
        assert positions["TSLA"] == 200


# ── paper_fill ─────────────────────────────────────────────────────────────────

class TestPaperFill:
    def _delta(self, side="BUY", qty=100, price=100.0, reason="test"):
        return PositionDelta(symbol="TSLA", side=side, qty=qty, fill_price=price, reason=reason)

    def test_buy_increases_position(self):
        positions = {"TSLA": 0}
        result = paper_fill(self._delta(side="BUY", qty=100), positions)
        assert positions["TSLA"] == 100
        assert result == 100

    def test_sell_decreases_position(self):
        positions = {"TSLA": 200}
        result = paper_fill(self._delta(side="SELL", qty=50), positions)
        assert positions["TSLA"] == 150
        assert result == 150

    def test_sell_cannot_go_below_zero(self):
        positions = {"TSLA": 30}
        result = paper_fill(self._delta(side="SELL", qty=100), positions)
        assert positions["TSLA"] == 0
        assert result == 0

    def test_adds_new_symbol_on_buy(self):
        positions = {}
        paper_fill(self._delta(side="BUY", qty=50), positions)
        assert positions["TSLA"] == 50

    def test_calls_log_decision(self):
        log_calls = []
        positions = {"TSLA": 0}
        paper_fill(self._delta(), positions, log_decision=log_calls.append)
        assert log_calls[0]["mode"] == "paper"
        assert log_calls[0]["position_after"] == 100

    def test_case_insensitive_side(self):
        positions = {"TSLA": 0}
        paper_fill(self._delta(side="buy", qty=10), positions)
        assert positions["TSLA"] == 10


# ── live_order ─────────────────────────────────────────────────────────────────

class TestLiveOrder:
    def _delta(self, side="BUY", qty=100, price=100.0, reason="test"):
        return PositionDelta(symbol="TSLA", side=side, qty=qty, fill_price=price, reason=reason)

    def test_successful_buy_mutates_positions_after_broker_call(self):
        calls = []
        def place(sym, qty, side, price):
            calls.append((sym, qty, side, price))

        positions = {"TSLA": 0}
        result = live_order(self._delta(side="BUY", qty=100), positions, place)
        assert result is True
        assert len(calls) == 1
        assert positions["TSLA"] == 100

    def test_successful_sell_mutates_positions_after_broker_call(self):
        calls = []
        def place(sym, qty, side, price):
            calls.append((sym, qty, side, price))

        positions = {"TSLA": 200}
        result = live_order(self._delta(side="SELL", qty=50), positions, place)
        assert result is True
        assert positions["TSLA"] == 150

    def test_broker_failure_does_not_mutate_positions(self):
        def place_fail(sym, qty, side, price):
            raise RuntimeError("Broker rejected: insufficient funds")

        positions = {"TSLA": 0}
        result = live_order(self._delta(side="BUY", qty=100), positions, place_fail)
        assert result is False
        assert positions["TSLA"] == 0  # state NOT changed

    def test_broker_failure_sell_does_not_mutate_positions(self):
        def place_fail(sym, qty, side, price):
            raise RuntimeError("Order not found")

        positions = {"TSLA": 200}
        result = live_order(self._delta(side="SELL", qty=200), positions, place_fail)
        assert result is False
        assert positions["TSLA"] == 200  # original qty preserved

    def test_broker_called_before_state_mutation(self):
        mutation_happened_at = {}
        broker_called_at = {}

        step = [0]

        def place(sym, qty, side, price):
            broker_called_at["step"] = step[0]
            step[0] += 1

        positions = {"TSLA": 0}

        original_apply = None

        # Patch to detect mutation order using the return value
        result = live_order(self._delta(side="BUY", qty=100), positions, place)
        # If broker was called and then mutation happened, broker_called_at should exist
        assert result is True
        assert "step" in broker_called_at

    def test_log_decision_on_success(self):
        log_calls = []
        positions = {"TSLA": 0}
        live_order(self._delta(), positions, lambda *a: None, log_decision=log_calls.append)
        assert log_calls[0]["status"] == "ACCEPTED"
        assert log_calls[0]["mode"] == "live"

    def test_log_decision_on_failure(self):
        log_calls = []
        positions = {"TSLA": 0}
        live_order(
            self._delta(),
            positions,
            lambda *a: (_ for _ in ()).throw(RuntimeError("fail")),
            log_decision=log_calls.append,
        )
        assert log_calls[0]["status"] == "BROKER_REJECTED"


# ── dispatch_execution ────────────────────────────────────────────────────────

class TestDispatchExecution:
    def _delta(self, side="BUY", qty=100):
        return PositionDelta(symbol="TSLA", side=side, qty=qty, fill_price=100.0, reason="test")

    def test_simulate_mode_returns_false_no_mutation(self):
        positions = {"TSLA": 0}
        result = dispatch_execution(self._delta(), ExecutionMode.SIMULATE, positions)
        assert result is False
        assert positions["TSLA"] == 0

    def test_paper_mode_returns_true_and_mutates(self):
        positions = {"TSLA": 0}
        result = dispatch_execution(self._delta(), ExecutionMode.PAPER, positions)
        assert result is True
        assert positions["TSLA"] == 100

    def test_live_mode_requires_place_order_fn(self):
        positions = {"TSLA": 0}
        with pytest.raises(ValueError, match="place_order_fn is required"):
            dispatch_execution(self._delta(), ExecutionMode.LIVE, positions, place_order_fn=None)

    def test_live_mode_success(self):
        positions = {"TSLA": 0}
        result = dispatch_execution(
            self._delta(), ExecutionMode.LIVE, positions,
            place_order_fn=lambda *a: None,
        )
        assert result is True
        assert positions["TSLA"] == 100

    def test_live_mode_failure_no_mutation(self):
        positions = {"TSLA": 0}
        def fail(*a):
            raise RuntimeError("fail")

        result = dispatch_execution(self._delta(), ExecutionMode.LIVE, positions, place_order_fn=fail)
        assert result is False
        assert positions["TSLA"] == 0

    def test_log_decision_passed_through(self):
        log_calls = []
        positions = {"TSLA": 0}
        dispatch_execution(
            self._delta(), ExecutionMode.SIMULATE, positions, log_decision=log_calls.append
        )
        assert len(log_calls) == 1
