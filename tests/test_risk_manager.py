from v3_pipeline.risk.manager import RiskConfig, RiskController


def test_ror_gate_uses_survival_threshold_semantics():
    controller = RiskController(config=RiskConfig(ruin_threshold=0.15))

    # Force edge<=0 => ror=1.0 => survival=0.0, should be blocked.
    allowed = controller.allow_trade_with_ror(
        win_rate=0.49,
        reward_risk_ratio=0.8,
        risk_fraction=0.05,
        mc_var_95=0.0,
    )

    assert allowed is False


def test_ror_gate_allows_positive_edge_under_default_threshold():
    controller = RiskController(config=RiskConfig(ruin_threshold=0.15))

    allowed = controller.allow_trade_with_ror(
        win_rate=0.62,
        reward_risk_ratio=1.8,
        risk_fraction=0.02,
        mc_var_95=0.0,
    )

    assert allowed is True


def test_daily_loss_limit_blocks_when_equity_breaches_boundary():
    controller = RiskController(config=RiskConfig(max_daily_loss_fraction=0.02))

    allowed = controller.allow_daily_loss(day_start_equity=100_000.0, current_equity=97_000.0)

    assert allowed is False


def test_var_passes_but_cvar_fails_gate():
    controller = RiskController(config=RiskConfig(max_trade_var_95=0.03, max_trade_cvar_95=0.04))

    allowed = controller.allow_trade_with_var_cvar(
        mc_var_95=-0.02,
        return_samples=[-0.10, -0.07, -0.03, -0.02, 0.01, 0.01, 0.02, 0.01, 0.00, 0.01],
    )

    assert allowed is False


def test_var_and_cvar_both_pass_gate():
    controller = RiskController(config=RiskConfig(max_trade_var_95=0.03, max_trade_cvar_95=0.04))

    allowed = controller.allow_trade_with_var_cvar(
        mc_var_95=-0.02,
        return_samples=[-0.035, -0.03, -0.02, -0.01, 0.01, 0.02, 0.01, 0.01, 0.00, 0.01],
    )

    assert allowed is True
