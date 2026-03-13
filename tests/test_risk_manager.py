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
