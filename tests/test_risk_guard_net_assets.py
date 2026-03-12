from risk_guard import RiskGuard


def _state(daily_pnl: float = 0.0):
    return {
        "date": "2099-01-01",
        "daily_pnl": daily_pnl,
        "daily_trades": 0,
        "consecutive_errors": 0,
        "circuit_broken": False,
        "circuit_break_time": None,
        "last_orders": {},
        "positions": {},
        "total_invested": 0.0,
    }


def test_daily_loss_limit_uses_runtime_net_assets():
    rg = RiskGuard()
    state = _state(daily_pnl=-50.0)

    # If runtime net assets is 1000, -50 means -5% and should trigger reject
    out = rg.check(
        state=state,
        code="US.TSLA",
        signal="BUY",
        price=100.0,
        available_cash=1000.0,
        current_positions={},
        net_assets=1000.0,
    )

    assert out["ok"] is False
    assert out["reason"] == "MaxDailyLoss"
