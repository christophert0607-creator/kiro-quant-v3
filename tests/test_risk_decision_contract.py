"""Acceptance tests for RiskDecision structured contract.

Every blocked trade must have a machine-readable reason, gate_name,
and metrics so downstream observers can classify gate failures without
parsing log strings.

Covered gates:
- circuit_breaker
- daily_loss
- ror (risk of ruin)
- var_cvar
- position_cap
"""

import pytest
from v3_pipeline.risk.manager import RiskConfig, RiskController, RiskDecision


@pytest.fixture
def rc():
    return RiskController(RiskConfig(
        max_drawdown=0.20,
        ruin_threshold=0.15,
        max_trade_var_95=0.03,
        max_daily_loss_fraction=0.05,
    ))


# ── RiskDecision dataclass ────────────────────────────────────────────────────


def test_risk_decision_permit_allowed():
    d = RiskDecision.permit("test_gate", foo=1.0)
    assert d.allowed is True
    assert d.gate_name == "test_gate"
    assert d.metrics["foo"] == 1.0


def test_risk_decision_block_not_allowed():
    d = RiskDecision.block("test_gate", reason="too risky", bar=0.5)
    assert d.allowed is False
    assert d.gate_name == "test_gate"
    assert "too risky" in d.reason
    assert d.metrics["bar"] == 0.5


# ── circuit_breaker ───────────────────────────────────────────────────────────


def test_decide_circuit_breaker_permits_within_limit(rc):
    d = rc.decide_circuit_breaker(equity_peak=100_000, current_equity=85_000)
    assert d.allowed is True
    assert d.gate_name == "circuit_breaker"
    assert "drawdown" in d.metrics


def test_decide_circuit_breaker_blocks_at_limit(rc):
    d = rc.decide_circuit_breaker(equity_peak=100_000, current_equity=75_000)  # 25% drawdown
    assert d.allowed is False
    assert d.gate_name == "circuit_breaker"
    assert "drawdown" in d.reason or "drawdown" in str(d.metrics)
    assert d.metrics["drawdown"] == pytest.approx(0.25, abs=0.001)


def test_decide_circuit_breaker_permits_zero_peak(rc):
    d = rc.decide_circuit_breaker(equity_peak=0, current_equity=100)
    assert d.allowed is True


# ── daily_loss ────────────────────────────────────────────────────────────────


def test_decide_daily_loss_permits_within_limit(rc):
    d = rc.decide_daily_loss(day_start_equity=100_000, current_equity=96_000)  # 4% loss < 5% limit
    assert d.allowed is True
    assert d.gate_name == "daily_loss"
    assert "loss_fraction" in d.metrics


def test_decide_daily_loss_blocks_breach(rc):
    d = rc.decide_daily_loss(day_start_equity=100_000, current_equity=90_000)  # 10% > 5% limit
    assert d.allowed is False
    assert d.gate_name == "daily_loss"
    assert "loss_fraction" in d.metrics
    assert d.metrics["loss_fraction"] == pytest.approx(0.10, abs=0.001)
    assert "intraday loss" in d.reason


def test_decide_daily_loss_permits_zero_equity(rc):
    d = rc.decide_daily_loss(day_start_equity=0, current_equity=100)
    assert d.allowed is True


# ── ror (risk of ruin) ────────────────────────────────────────────────────────


def test_decide_ror_permits_good_edge(rc):
    d = rc.decide_ror(win_rate=0.6, reward_risk_ratio=2.0, risk_fraction=0.01, mc_var_95=-0.01)
    assert d.allowed is True
    assert d.gate_name == "ror"
    assert "ror" in d.metrics


def test_decide_ror_blocks_negative_edge():
    rc = RiskController(RiskConfig(ruin_threshold=0.15))
    d = rc.decide_ror(win_rate=0.3, reward_risk_ratio=0.5, risk_fraction=0.5, mc_var_95=-0.01)
    assert d.allowed is False
    assert d.gate_name == "ror"
    assert "survival" in d.reason or "threshold" in d.reason
    assert "survival" in d.metrics


def test_decide_ror_blocks_on_var_breach(rc):
    d = rc.decide_ror(win_rate=0.6, reward_risk_ratio=2.0, risk_fraction=0.01, mc_var_95=-0.10)
    assert d.allowed is False
    assert d.gate_name == "var_cvar"


# ── var_cvar ──────────────────────────────────────────────────────────────────


def test_decide_var_cvar_permits_acceptable_var(rc):
    d = rc.decide_var_cvar(mc_var_95=-0.01)
    assert d.allowed is True
    assert d.gate_name == "var_cvar"


def test_decide_var_cvar_blocks_excessive_var(rc):
    d = rc.decide_var_cvar(mc_var_95=-0.10)
    assert d.allowed is False
    assert d.gate_name == "var_cvar"
    assert "VaR" in d.reason
    assert d.metrics["var_95"] == pytest.approx(-0.10)


def test_decide_var_cvar_blocks_excessive_cvar():
    rc = RiskController(RiskConfig(max_trade_var_95=0.03, max_trade_cvar_95=0.02))
    d = rc.decide_var_cvar(mc_var_95=-0.02, tail_losses_95=[-0.05, -0.06, -0.07])
    assert d.allowed is False
    assert d.gate_name == "var_cvar"
    assert "CVaR" in d.reason


# ── position_cap ──────────────────────────────────────────────────────────────


def test_decide_position_cap_permits_under_limit(rc):
    d = rc.decide_position_cap(active_positions=5, max_positions=15)
    assert d.allowed is True
    assert d.gate_name == "position_cap"
    assert d.metrics["active_positions"] == 5


def test_decide_position_cap_blocks_at_limit(rc):
    d = rc.decide_position_cap(active_positions=15, max_positions=15)
    assert d.allowed is False
    assert d.gate_name == "position_cap"
    assert "15" in d.reason
    assert d.metrics["active_positions"] == 15


def test_decide_position_cap_blocks_over_limit(rc):
    d = rc.decide_position_cap(active_positions=20, max_positions=15)
    assert d.allowed is False


# ── All blocked decisions have machine-readable fields ───────────────────────


@pytest.mark.parametrize("decision", [
    RiskDecision.block("circuit_breaker", reason="drawdown 25%", drawdown=0.25),
    RiskDecision.block("daily_loss", reason="intraday loss 10%", loss_fraction=0.10),
    RiskDecision.block("ror", reason="survival 0.05 < 0.15", survival=0.05),
    RiskDecision.block("var_cvar", reason="VaR -0.10 < limit -0.03", var_95=-0.10),
    RiskDecision.block("position_cap", reason="cap 15 reached", active_positions=15),
])
def test_blocked_decision_has_required_fields(decision):
    assert decision.allowed is False
    assert decision.gate_name  # non-empty string
    assert decision.reason  # non-empty
    assert isinstance(decision.metrics, dict)
