from types import SimpleNamespace

import pytest

from v3_pipeline.core.trade_intents import TradeAction, resolve_long_exit_intent


def cfg(**overrides):
    base = dict(
        stop_loss_pct=0.02,
        max_hold_bars=999,
        buy_cooldown_cycles=0,
        profit_protection_enabled=True,
        take_profit_activation_pct=0.03,
        partial_take_profit_enabled=True,
        partial_take_profit_fraction=0.30,
        strong_trend_partial_fraction=0.0,
        trailing_after_profit_pct=0.012,
        strong_trend_trailing_pct=0.020,
        weak_trend_trailing_pct=0.008,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_long_exit_intent_uses_broker_qty_when_internal_qty_zero():
    intent = resolve_long_exit_intent(
        symbol="QCOM",
        current_price=235.49,
        prediction=220.00,
        confidence=1.0,
        qty=806,
        entry_price=240.0,
        bars_held=5,
        threshold_down=233.13,
        swing_sell_signal=False,
        retry_reason=None,
        config=cfg(profit_protection_enabled=False),
        indicators={},
    )

    assert intent.action == TradeAction.EXIT_LONG
    assert intent.side == "SELL"
    assert intent.qty == 806
    assert intent.reason == "model_signal"
    assert intent.source == "model_sell"


def test_profit_activation_partial_exit_has_priority_over_model_and_swing_noise():
    intent = resolve_long_exit_intent(
        symbol="QCOM",
        current_price=103.50,
        prediction=101.00,
        confidence=0.7,
        qty=100,
        entry_price=100.0,
        bars_held=2,
        threshold_down=99.0,
        swing_sell_signal=True,
        retry_reason=None,
        config=cfg(),
        indicators={"SMA_5": 101.0, "SMA_20": 102.0, "MACD_HIST": -0.1, "RSI_14": 66.0},
    )

    assert intent.action == TradeAction.EXIT_LONG
    assert intent.qty == 30
    assert intent.priority == 95
    assert intent.reason == "partial_take_profit_0.0300"
    assert intent.metadata["profit_protection_activated"] is True
    assert intent.metadata["remaining_qty"] == 70
    assert intent.metadata["trailing_pct"] == pytest.approx(0.008)


def test_strong_trend_activates_profit_protection_without_forced_partial_sell():
    intent = resolve_long_exit_intent(
        symbol="QCOM",
        current_price=104.00,
        prediction=105.00,
        confidence=0.9,
        qty=100,
        entry_price=100.0,
        bars_held=2,
        threshold_down=99.0,
        swing_sell_signal=True,
        retry_reason=None,
        config=cfg(),
        indicators={"SMA_5": 103.0, "SMA_20": 101.0, "MACD_HIST": 0.4, "RSI_14": 68.0},
    )

    assert intent.action == TradeAction.HOLD
    assert intent.reason == "profit_protection_strong_trend_hold"
    assert intent.priority == 95
    assert intent.metadata["profit_protection_activated"] is True
    assert intent.metadata["strong_trend"] is True
    assert intent.metadata["trailing_pct"] == pytest.approx(0.020)


def test_profit_trailing_exit_sells_remainder_after_pullback():
    intent = resolve_long_exit_intent(
        symbol="QCOM",
        current_price=101.80,
        prediction=101.50,
        confidence=0.6,
        qty=70,
        entry_price=100.0,
        bars_held=5,
        threshold_down=99.0,
        swing_sell_signal=False,
        retry_reason=None,
        config=cfg(_profit_protection_active=True, _highest_price_since_entry=104.0),
        indicators={"SMA_5": 102.0, "SMA_20": 103.0, "MACD_HIST": -0.1, "RSI_14": 60.0},
    )

    assert intent.action == TradeAction.EXIT_LONG
    assert intent.qty == 70
    assert intent.reason == "trailing_profit_exit_0.0080"
    assert intent.metadata["trailing_high"] == pytest.approx(104.0)


def test_long_exit_has_priority_over_short_entry_signal():
    intent = resolve_long_exit_intent(
        symbol="QCOM",
        current_price=100.0,
        prediction=95.0,
        confidence=0.8,
        qty=50,
        entry_price=99.5,
        bars_held=4,
        threshold_down=99.0,
        swing_sell_signal=False,
        retry_reason=None,
        config=cfg(profit_protection_enabled=False),
        indicators={},
    )

    assert intent.action == TradeAction.EXIT_LONG
    assert intent.reason == "model_signal"
    assert intent.metadata["entry_blocked_until_flat"] is True


def test_hold_intent_records_reason_when_no_exit_signal():
    intent = resolve_long_exit_intent(
        symbol="QCOM",
        current_price=100.0,
        prediction=100.2,
        confidence=0.3,
        qty=50,
        entry_price=100.0,
        bars_held=2,
        threshold_down=99.0,
        swing_sell_signal=False,
        retry_reason=None,
        config=cfg(profit_protection_enabled=False),
        indicators={},
    )

    assert intent.action == TradeAction.HOLD
    assert intent.reason == "no_exit_signal"
    assert intent.metadata["qty"] == 50
