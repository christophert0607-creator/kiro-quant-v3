import pytest

from v3_pipeline.execution.cost_calculator import TransactionCostCalculator
from v3_pipeline.risk.kelly_sizer import KellyPositionSizer
from v3_pipeline.risk.rules import RiskRulesEngine


def test_kelly_fraction_matches_design_example():
    sizer = KellyPositionSizer()
    fraction = sizer.calculate_fraction(win_rate=0.55, avg_win=100, avg_loss=80)
    assert fraction == pytest.approx(0.095, rel=1e-6)


def test_kelly_position_value_uses_fraction():
    sizer = KellyPositionSizer()
    position_value = sizer.calculate_position_value(
        win_rate=0.55,
        avg_win=100,
        avg_loss=80,
        portfolio_value=100_000,
    )
    assert position_value == pytest.approx(9500.0, rel=1e-6)


def test_risk_rules_reject_when_daily_risk_exceeded():
    rules = RiskRulesEngine()
    assert (
        rules.can_open_trade(
            proposed_trade_risk_fraction=0.02,
            current_daily_risk_fraction=0.04,
            current_positions=3,
            current_exposure_fraction=0.3,
            proposed_exposure_fraction=0.1,
        )
        is False
    )


def test_transaction_cost_hk_includes_stamp_duty():
    calc = TransactionCostCalculator()
    costs = calc.calculate_total_cost(symbol="00700.HK", quantity=100, price=300.0, market_cap="large_cap")

    assert costs["commission"] == pytest.approx(30.0)
    assert costs["stamp_duty"] == pytest.approx(30.0)
    assert costs["slippage"] == pytest.approx(15.0)
    assert costs["total_cost"] == pytest.approx(75.0)


def test_transaction_cost_gate_blocks_when_edge_too_small():
    calc = TransactionCostCalculator()
    assert calc.should_execute_trade(expected_return_rate=0.002, estimated_cost_rate=0.002, cost_buffer=0.2) is False
