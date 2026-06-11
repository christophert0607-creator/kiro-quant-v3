from types import SimpleNamespace

import pandas as pd

from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop


class DummyRiskController:
    def circuit_breaker_triggered(self, *_args, **_kwargs):
        return False

    def allow_trade_with_ror(self, *_args, **_kwargs):
        return True

    def allow_daily_loss(self, *_args, **_kwargs):
        return True


class DummyLogger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass

    def debug(self, *_args, **_kwargs):
        pass


def make_loop():
    cfg = LiveConfig(
        symbols_list=["QCOM"],
        auto_trade=True,
        paper_trading=True,
        prediction_thresholds={"QCOM": 0.01},
        swing_strategy_enabled=False,
        diagnostics_verbose=True,
        log_trade_decisions=True,
        buy_cooldown_cycles=0,
    )
    loop = LiveTradingLoop.__new__(LiveTradingLoop)
    loop.config = cfg
    loop.account_value = 1_000_000.0
    loop.equity_peak = 1_000_000.0
    loop.risk_controller = DummyRiskController()
    loop.position_qty_by_symbol = {"QCOM": 0}
    loop.broker_position_qty_by_symbol = {"QCOM": 806}
    loop.entry_price_by_symbol = {}
    loop.entry_rsi_by_symbol = {}
    loop.bars_held_by_symbol = {"QCOM": 0}
    loop.cycles_since_buy_by_symbol = {"QCOM": 999}
    loop.highest_price_since_entry_by_symbol = {}
    loop.short_position_qty_by_symbol = {"QCOM": 0}
    loop.bars_held_short_by_symbol = {"QCOM": 0}
    loop.cycles_since_short_by_symbol = {"QCOM": 999}
    loop.buy_cover_signal_streak_by_symbol = {"QCOM": 0}
    loop.sell_retry_required_by_symbol = {}
    loop.logger = DummyLogger()
    loop._notify = lambda *_args, **_kwargs: None
    loop._get_buffer = lambda _symbol: pd.DataFrame({"Close": [230.0, 229.0, 228.0]})
    loop._evaluate_swing_signal = lambda *_args, **_kwargs: {"buy_signal": False, "sell_signal": False}
    loop._entry_gates_allow = lambda *_args, **_kwargs: True
    loop._refresh_daily_loss_anchor = lambda: None
    loop.day_start_equity = 1_000_000.0
    calls = []
    loop._execute = lambda *args, **kwargs: calls.append((args, kwargs))
    loop._test_execute_calls = calls
    return loop


def test_broker_held_symbol_is_added_to_cycle_universe_for_exit_safety():
    loop = make_loop()
    loop.symbols = ["NVDA"]
    loop.market_buffers = {}

    symbol = loop._normalize_broker_code_to_symbol("US.INTC")
    loop._ensure_tracking_symbol(symbol)
    loop.broker_position_qty_by_symbol[symbol] = 2166

    assert symbol == "INTC"
    assert "INTC" in loop.symbols
    assert loop.position_qty_by_symbol["INTC"] == 0
    assert loop._effective_long_qty_for_exit("INTC") == 2166


def test_model_sell_uses_broker_qty_when_internal_qty_zero():
    loop = make_loop()

    loop._run_trading_logic_bridge(
        symbol="QCOM",
        current_price=235.49,
        prediction=220.00,
        confidence=1.0,
        allow_long=True,
        latest_frame=None,
        pattern_label="DownTrend",
        pattern_confidence=1.0,
    )

    assert loop._test_execute_calls, "expected SELL to be executed using broker qty"
    args, _kwargs = loop._test_execute_calls[0]
    assert args[0] == "QCOM"
    assert args[1] == "SELL"
    assert args[2] == 806
    assert args[4] == "model_signal"


def test_single_order_loss_over_5_percent_notifies_agent_once():
    loop = make_loop()
    notifications = []
    loop._notify = notifications.append
    loop.entry_price_by_symbol["QCOM"] = 100.0
    loop.config.stop_loss_pct = 0.0
    loop.config.single_order_loss_alert_pct = 0.05

    loop._run_trading_logic_bridge(
        symbol="QCOM",
        current_price=94.9,
        prediction=110.0,
        confidence=0.8,
        allow_long=True,
    )
    loop._run_trading_logic_bridge(
        symbol="QCOM",
        current_price=94.5,
        prediction=110.0,
        confidence=0.8,
        allow_long=True,
    )

    matching = [msg for msg in notifications if "[LOSS_ALERT_SINGLE_ORDER]" in msg]
    assert len(matching) == 1
    assert "QCOM" in matching[0]
    assert "loss=-5.10%" in matching[0]
    assert "qty=806" in matching[0]


def test_stop_loss_uses_broker_qty_when_internal_qty_zero():
    loop = make_loop()
    loop.entry_price_by_symbol["QCOM"] = 240.0
    loop.config.stop_loss_pct = 0.02

    loop._run_trading_logic_bridge(
        symbol="QCOM",
        current_price=235.0,
        prediction=250.0,
        confidence=0.8,
        allow_long=True,
    )

    assert loop._test_execute_calls
    args, _kwargs = loop._test_execute_calls[0]
    assert args[1] == "SELL"
    assert args[2] == 806
    assert args[4].startswith("stop_loss_")


def test_sell_exit_not_blocked_by_order_rate_limit():
    loop = make_loop()
    loop.config.max_orders_per_cycle = 0
    loop.config.order_throttle_seconds = 9999
    loop.entry_price_by_symbol["QCOM"] = 240.0

    loop._run_trading_logic_bridge(
        symbol="QCOM",
        current_price=235.0,
        prediction=220.0,
        confidence=1.0,
        allow_long=True,
    )

    assert loop._test_execute_calls
    assert loop._test_execute_calls[0][0][1] == "SELL"


def test_sell_retry_marker_forces_next_cycle_sell():
    loop = make_loop()
    loop.sell_retry_required_by_symbol = {"QCOM": "model_signal"}

    loop._run_trading_logic_bridge(
        symbol="QCOM",
        current_price=235.49,
        prediction=250.0,
        confidence=0.5,
        allow_long=True,
    )

    assert loop._test_execute_calls
    args, _kwargs = loop._test_execute_calls[0]
    assert args[1] == "SELL"
    assert args[2] == 806
    assert args[4] == "retry_model_signal"
