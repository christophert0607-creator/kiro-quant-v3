"""Tests for Kelly position sizing integration in main_loop."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from v3_pipeline.risk.kelly_sizer import KellyPositionSizer, KellySizingConfig


# ── KellyPositionSizer unit tests ─────────────────────────────────────────────

class TestKellyPositionSizerDetails:
    def test_calculate_details_positive_edge(self):
        sizer = KellyPositionSizer()
        d = sizer.calculate_details(win_rate=0.6, avg_win=0.02, avg_loss=0.01)
        assert d["zero_edge"] is False
        assert d["capped_fraction"] > 0
        assert d["kelly_raw"] > 0

    def test_calculate_details_negative_edge(self):
        sizer = KellyPositionSizer()
        d = sizer.calculate_details(win_rate=0.2, avg_win=0.01, avg_loss=0.05)
        assert d["zero_edge"] is True
        assert d["capped_fraction"] == 0.0

    def test_calculate_details_no_avg_loss(self):
        sizer = KellyPositionSizer()
        d = sizer.calculate_details(win_rate=0.7, avg_win=0.02, avg_loss=0.0)
        assert d["zero_edge"] is True
        assert d["capped_fraction"] == 0.0

    def test_calculate_details_no_avg_win(self):
        sizer = KellyPositionSizer()
        d = sizer.calculate_details(win_rate=0.7, avg_win=0.0, avg_loss=0.01)
        assert d["zero_edge"] is True

    def test_calculate_details_respects_max_fraction(self):
        sizer = KellyPositionSizer(KellySizingConfig(kelly_factor=1.0, max_fraction=0.05))
        d = sizer.calculate_details(win_rate=0.9, avg_win=0.10, avg_loss=0.01)
        assert d["capped_fraction"] <= 0.05

    def test_calculate_fraction_delegates_to_details(self):
        sizer = KellyPositionSizer()
        frac = sizer.calculate_fraction(win_rate=0.6, avg_win=0.02, avg_loss=0.01)
        details = sizer.calculate_details(win_rate=0.6, avg_win=0.02, avg_loss=0.01)
        assert frac == details["capped_fraction"]

    def test_calculate_details_keys(self):
        sizer = KellyPositionSizer()
        d = sizer.calculate_details(win_rate=0.55, avg_win=0.015, avg_loss=0.01)
        assert "kelly_raw" in d
        assert "kelly_scaled" in d
        assert "capped_fraction" in d
        assert "zero_edge" in d

    def test_kelly_factor_scales_raw(self):
        sizer = KellyPositionSizer(KellySizingConfig(kelly_factor=0.5, max_fraction=1.0))
        d = sizer.calculate_details(win_rate=0.6, avg_win=0.02, avg_loss=0.01)
        assert abs(d["kelly_scaled"] - d["kelly_raw"] * 0.5) < 1e-9


# ── Kelly integration: zero-edge blocks BUY in main_loop ─────────────────────

def _build_loop_with_kelly_data(avg_win: float, avg_loss: float, win_rate: float = 0.55):
    """Build a LiveTradingLoop stub whose MC always returns the given Kelly inputs."""
    from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop

    cfg = LiveConfig(
        symbols_list=["AAPL"],
        max_portfolio_positions=10,
        swing_strategy_enabled=False,
        prediction_threshold=0.01,
    )
    loop = LiveTradingLoop.__new__(LiveTradingLoop)
    loop.config = cfg
    loop.logger = MagicMock()
    loop.position_qty_by_symbol = {"AAPL": 0}
    loop.short_position_qty_by_symbol = {"AAPL": 0}
    loop.last_price_by_symbol = {"AAPL": 100.0}
    loop.entry_price_by_symbol = {"AAPL": 0.0}
    loop.short_entry_price_by_symbol = {}
    loop.cycles_since_buy_by_symbol = {}
    loop.bars_held_by_symbol = {}
    loop.equity_peak = 100000.0
    loop.account_value = 100000.0
    loop.day_start_equity = 100000.0
    loop.symbols = ["AAPL"]
    loop.sentiment_score = 0.0
    loop._decision_trace_buffer = []
    loop._kelly_sizer = KellyPositionSizer()

    # Build a market buffer with enough data for stress_test
    prices = [100.0 + i * 0.1 for i in range(60)]
    loop.market_buffers = {"AAPL": pd.DataFrame({"Close": prices})}

    # Stub Monte Carlo to return controlled values
    mock_mc = MagicMock()
    mock_mc.stress_test.return_value = {
        "expected": 0.001, "var95": -0.02, "cvar95": -0.03,
        "win_rate": win_rate, "avg_win": avg_win, "avg_loss": avg_loss,
    }
    loop.monte_carlo = mock_mc

    mock_strategy = MagicMock()
    mock_strategy.confidence_to_risk_pct.return_value = 0.05
    loop.strategy_factory = mock_strategy

    mock_risk = MagicMock()
    mock_risk.allow_trade_with_ror.return_value = True
    mock_risk.circuit_breaker_triggered.return_value = False
    mock_risk.allow_daily_loss.return_value = True
    loop.risk_controller = mock_risk

    return loop


class TestKellyZeroEdgeBlocksBuy:
    def test_positive_edge_produces_nonzero_alloc(self):
        """Kelly fraction > 0 with positive edge leads to BUY execution."""
        loop = _build_loop_with_kelly_data(avg_win=0.02, avg_loss=0.01, win_rate=0.6)

        executed = []
        loop._execute = lambda sym, side, qty, price, reason: executed.append((side, qty))  # type: ignore
        loop._append_decision_trace = MagicMock()
        loop._refresh_daily_loss_anchor = MagicMock()
        loop._round_to_lot = lambda qty, sym: qty

        with patch.object(loop, "_evaluate_swing_signal", return_value={"buy_signal": False, "sell_signal": False}):
            loop.check_and_trade(
                    symbol="AAPL", current_price=100.0, prediction=102.0,
                    confidence=0.8, allow_long=True,
                )

        assert len(executed) > 0, "Expected BUY with positive Kelly edge"
        assert executed[0][0] == "BUY"

    def test_negative_edge_skips_buy(self):
        """Kelly fraction = 0 with negative edge skips BUY and logs KELLY_ZERO_EDGE."""
        loop = _build_loop_with_kelly_data(avg_win=0.01, avg_loss=0.05, win_rate=0.2)

        executed = []
        loop._execute = lambda sym, side, qty, price, reason: executed.append((side, qty))  # type: ignore
        loop._append_decision_trace = MagicMock()
        loop._refresh_daily_loss_anchor = MagicMock()
        loop._round_to_lot = lambda qty, sym: qty

        with patch.object(loop, "_evaluate_swing_signal", return_value={"buy_signal": False, "sell_signal": False}):
            loop.check_and_trade(
                    symbol="AAPL", current_price=100.0, prediction=102.0,
                    confidence=0.8, allow_long=True,
                )

        assert len(executed) == 0, "Expected no BUY with negative Kelly edge"

    def test_no_avg_loss_data_falls_back_to_confidence(self):
        """When avg_loss=0 (insufficient simulation data), falls back to confidence sizing."""
        loop = _build_loop_with_kelly_data(avg_win=0.02, avg_loss=0.0, win_rate=0.8)

        executed = []
        loop._execute = lambda sym, side, qty, price, reason: executed.append((side, qty))  # type: ignore
        loop._append_decision_trace = MagicMock()
        loop._refresh_daily_loss_anchor = MagicMock()
        loop._round_to_lot = lambda qty, sym: qty

        with patch.object(loop, "_evaluate_swing_signal", return_value={"buy_signal": False, "sell_signal": False}):
            loop.check_and_trade(
                    symbol="AAPL", current_price=100.0, prediction=102.0,
                    confidence=0.8, allow_long=True,
                )

        assert len(executed) > 0, "Should fall back to confidence sizing when avg_loss=0"
        loop.strategy_factory.confidence_to_risk_pct.assert_called()
