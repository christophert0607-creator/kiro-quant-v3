from v3_pipeline.core.trade_quality import (
    TradeQualityDecision,
    TradeQualityFilter,
    TradeQualityInput,
    PositionContext,
    RecentStats,
)


def _inp(**overrides):
    base = dict(
        symbol="TSLA",
        market="US",
        action="BUY",
        prediction=103.0,
        current_price=100.0,
        confidence=0.85,
        indicators={"RSI_14": 45.0, "MACD_HIST": 0.5, "BB_POSITION": 0.35, "ATR_14": 2.0},
        position=PositionContext(current_qty=0, current_notional=0.0, cap_remaining=50_000.0),
        recent=RecentStats(win_rate=0.62, prediction_mae_pct=0.02, turnover_penalty=0.0),
        min_score=0.65,
        shadow_min_score=0.50,
    )
    base.update(overrides)
    return TradeQualityInput(**base)


def test_high_confidence_strong_move_without_concentration_accepts():
    result = TradeQualityFilter().score(_inp())
    assert result.decision == TradeQualityDecision.ACCEPT
    assert result.score >= 0.65


def test_low_confidence_rejects():
    result = TradeQualityFilter().score(_inp(confidence=0.05, prediction=100.5))
    assert result.decision == TradeQualityDecision.REJECT
    assert "low_confidence" in result.reasons


def test_position_over_cap_rejects():
    result = TradeQualityFilter().score(
        _inp(position=PositionContext(current_qty=100, current_notional=60_000.0, cap_remaining=0.0))
    )
    assert result.decision == TradeQualityDecision.REJECT
    assert "position_cap_exhausted" in result.reasons


def test_high_turnover_penalty_rejects():
    result = TradeQualityFilter().score(_inp(recent=RecentStats(win_rate=0.55, prediction_mae_pct=0.03, turnover_penalty=0.95)))
    assert result.decision == TradeQualityDecision.REJECT
    assert "high_turnover_penalty" in result.reasons


def test_hk_lot_size_impossible_after_cap_rejects():
    result = TradeQualityFilter().score(
        _inp(
            symbol="0700.HK",
            market="HK",
            current_price=400.0,
            prediction=410.0,
            position=PositionContext(current_qty=0, current_notional=0.0, cap_remaining=20_000.0, lot_size=100),
        )
    )
    assert result.decision == TradeQualityDecision.REJECT
    assert "hk_lot_cap_insufficient" in result.reasons


def test_semi_enforce_blocks_obvious_reject_reasons():
    from types import SimpleNamespace
    from v3_pipeline.core.main_loop import LiveTradingLoop
    from v3_pipeline.core.trade_quality import TradeQualityResult

    loop = object.__new__(LiveTradingLoop)
    loop.config = SimpleNamespace(trade_quality_mode="semi_enforce", trade_quality_semi_enforce_min_score=0.50)
    result = TradeQualityResult(
        decision=TradeQualityDecision.REJECT,
        score=0.82,
        reasons=["prediction_direction_mismatch"],
        components={},
    )

    assert loop._trade_quality_allows_entry(result) is False


def test_semi_enforce_allows_non_obvious_shadow_reject_above_floor():
    from types import SimpleNamespace
    from v3_pipeline.core.main_loop import LiveTradingLoop
    from v3_pipeline.core.trade_quality import TradeQualityResult

    loop = object.__new__(LiveTradingLoop)
    loop.config = SimpleNamespace(trade_quality_mode="semi_enforce", trade_quality_semi_enforce_min_score=0.50)
    result = TradeQualityResult(
        decision=TradeQualityDecision.REJECT,
        score=0.55,
        reasons=["quality_ok"],
        components={},
    )

    assert loop._trade_quality_allows_entry(result) is True


def test_semi_enforce_blocks_low_score_even_without_hard_reason():
    from types import SimpleNamespace
    from v3_pipeline.core.main_loop import LiveTradingLoop
    from v3_pipeline.core.trade_quality import TradeQualityResult

    loop = object.__new__(LiveTradingLoop)
    loop.config = SimpleNamespace(trade_quality_mode="semi_enforce", trade_quality_semi_enforce_min_score=0.50)
    result = TradeQualityResult(
        decision=TradeQualityDecision.REJECT,
        score=0.49,
        reasons=["quality_ok"],
        components={},
    )

    assert loop._trade_quality_allows_entry(result) is False
