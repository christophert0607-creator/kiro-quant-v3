import asyncio
import logging
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pandas as pd


@dataclass
class _ImportStubPreparer:
    lookback: int = 2
    target_col: str = "Close"
    is_fitted: bool = True
    feature_columns: list[str] | None = None

    def fit_transform(self, frame):
        self.is_fitted = True
        self.feature_columns = [c for c in frame.columns if c != "Date"]
        return frame


sys.modules.setdefault("requests", SimpleNamespace(post=lambda *args, **kwargs: None))
sys.modules.setdefault(
    "v3_pipeline.models.manager",
    SimpleNamespace(DataPreparer=lambda **kwargs: _ImportStubPreparer(**kwargs), ModelManager=object),
)

from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop


class _DummyModelManager:
    def __init__(self, prediction: float):
        self.data_preparer = _ImportStubPreparer()
        self._prediction = prediction

    def predict(self, *_args, **_kwargs):
        return self._prediction

    def predict_pattern(self, *_args, **_kwargs):
        return {"pattern": "Unknown", "confidence": 0.0, "probs": {"Unknown": 1.0}}




class _DummyModelManagerNoPattern:
    def __init__(self, prediction: float):
        self.data_preparer = _ImportStubPreparer()
        self._prediction = prediction

    def predict(self, *_args, **_kwargs):
        return self._prediction



class _DummyModelManagerBadPatternPayload:
    def __init__(self, prediction: float):
        self.data_preparer = _ImportStubPreparer()
        self._prediction = prediction

    def predict(self, *_args, **_kwargs):
        return self._prediction

    def predict_pattern(self, *_args, **_kwargs):
        return "not_a_dict"



class _DummyModelManagerBadPatternConfidence:
    def __init__(self, prediction: float):
        self.data_preparer = _ImportStubPreparer()
        self._prediction = prediction

    def predict(self, *_args, **_kwargs):
        return self._prediction

    def predict_pattern(self, *_args, **_kwargs):
        return {"pattern": "DoubleBottom", "confidence": "nan!"}

class _DummyRiskController:
    def __init__(self, allow_ror: bool = True, allow_daily: bool = True, allow_var_cvar: bool = True):
        self.allow_ror = allow_ror
        self.allow_daily = allow_daily
        self.allow_var_cvar = allow_var_cvar
        self.config = SimpleNamespace(max_daily_loss_fraction=1.0)

    def circuit_breaker_triggered(self, *_args, **_kwargs):
        return False

    def allow_trade_with_ror(self, *_args, **_kwargs):
        return self.allow_ror

    def allow_daily_loss(self, *_args, **_kwargs):
        return self.allow_daily

    def allow_trade_with_var_cvar(self, *_args, **_kwargs):
        return self.allow_var_cvar

    def estimate_cvar_95(self, *_args, **_kwargs):
        return -0.05


class _StrictDailyLossRiskController(_DummyRiskController):
    def __init__(self):
        super().__init__(allow_daily=True)
        self.daily_loss_args: dict[str, float] | None = None

    def allow_daily_loss(self, *, day_start_equity, current_equity):
        self.daily_loss_args = {
            "day_start_equity": float(day_start_equity),
            "current_equity": float(current_equity),
        }
        return True


class _DummyConnector:
    class config:
        market_prefix = "US"

    def connect(self):
        return None

    def close(self):
        return None

    def heartbeat(self):
        return True

    def get_sync_assets(self):
        return {"total_assets": 100000.0}

    def get_sync_positions(self):
        return pd.DataFrame(columns=["code", "qty", "can_sell_qty"])

    def get_latest_quote(self, symbol):
        return {
            "Date": pd.Timestamp("2026-03-13T10:00:00Z"),
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.0,
            "Volume": 10_000,
            "data_source": "test",
            "symbol": symbol,
        }

    def get_order_reference_price(self, _symbol, _side, fallback_price):
        return fallback_price

    def get_open_orders(self):
        return pd.DataFrame([])


class _DummyFailingConnector(_DummyConnector):
    def heartbeat(self):
        raise RuntimeError("broker down")

    def reconnect(self, **_kwargs):
        raise RuntimeError("reconnect failed")


class _DummyDataManager:
    def __init__(self, payload):
        self.payload = payload

    def get_market_data(self, _symbol):
        return self.payload


class _ZeroEdgeMonteCarlo:
    def stress_test(self, _returns):
        return {"win_rate": 0.30, "avg_win": 0.01, "avg_loss": 0.02, "var95": -0.05}


class _ZeroEdgeKellySizer:
    def calculate_details(self, **_kwargs):
        return {"zero_edge": True, "kelly_raw": -0.1, "capped_fraction": 0.0}


class _PassFeatureGenerator:
    def generate(self, frame):
        return frame


class _PassAlphaEngine:
    def select_features(self, _symbol, featured):
        return featured


def _build_loop(prediction: float, auto_trade: bool = False, allow_ror: bool = True, allow_daily: bool = True, allow_var_cvar: bool = True, futu_connector=None, data_manager=None):
    model_manager = _DummyModelManager(prediction=prediction)
    cfg = LiveConfig(
        symbol="TSLA",
        symbols_list=["TSLA"],
        prediction_threshold=0.01,
        prediction_thresholds={"TSLA": 0.01},
        auto_trade=auto_trade,
        paper_trading=True,
        log_trade_decisions=True,
        polling_seconds=1,
    )
    loop = LiveTradingLoop(
        model_manager=model_manager,
        risk_controller=_DummyRiskController(allow_ror=allow_ror, allow_daily=allow_daily, allow_var_cvar=allow_var_cvar),
        futu_connector=futu_connector or _DummyConnector(),
        data_manager=data_manager,
        feature_generator=_PassFeatureGenerator(),
        config=cfg,
    )
    loop.alpha_engine = _PassAlphaEngine()
    loop.market_buffers["TSLA"] = pd.DataFrame(
        [
            {
                "Date": pd.Timestamp("2026-03-13T09:59:00Z"),
                "Open": 99.0,
                "High": 100.0,
                "Low": 98.0,
                "Close": 99.5,
                "Volume": 9000,
                "data_source": "seed",
            },
            {
                "Date": pd.Timestamp("2026-03-13T09:59:30Z"),
                "Open": 99.5,
                "High": 100.1,
                "Low": 99.1,
                "Close": 100.0,
                "Volume": 9200,
                "data_source": "seed",
            },
        ]
    )
    return loop


def test_run_symbol_cycle_calls_check_and_trade(monkeypatch):
    loop = _build_loop(prediction=102.5)
    called = {"count": 0}

    def _fake_check(*_args, **_kwargs):
        called["count"] += 1

    monkeypatch.setattr(loop, "check_and_trade", _fake_check)

    asyncio.run(loop._run_symbol_cycle("TSLA"))

    assert called["count"] == 1


def test_trade_logic_logs_profile_gate_when_long_disabled():
    loop = _build_loop(prediction=102.5)
    messages: list[str] = []

    def _capture_info(msg, *args, **kwargs):
        rendered = msg % args if args else msg
        messages.append(str(rendered))

    loop.logger.info = _capture_info  # type: ignore[assignment]
    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=102.5,
        confidence=0.8,
        allow_long=False,
    )

    assert any("TRADE_CHECK[TSLA]" in line for line in messages)
    assert any("PROFILE_GATE[TSLA] blocked BUY" in line for line in messages)


def test_trade_logic_swing_buy_signal_triggers_entry():
    loop = _build_loop(prediction=100.2)
    executed: list[tuple[str, int, str]] = []

    def _fake_execute(symbol, side, qty, price, reason):
        executed.append((side, qty, reason))

    loop._execute = _fake_execute  # type: ignore[assignment]
    latest = pd.DataFrame(
        [
            {"Date": pd.Timestamp("2026-03-13T09:58:00Z"), "Low": 99.5, "High": 101.0, "Close": 100.4, "RSI_14": 40.0, "MACD": -0.2, "MACD_SIGNAL": -0.1},
            {"Date": pd.Timestamp("2026-03-13T09:59:00Z"), "Low": 98.9, "High": 100.8, "Close": 100.0, "RSI_14": 25.0, "MACD": -0.05, "MACD_SIGNAL": -0.08},
        ]
    )

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=100.2,
        confidence=0.8,
        allow_long=True,
        latest_frame=latest,
    )

    assert executed
    assert executed[0][0] == "BUY"
    assert "swing_signal" in executed[0][2]


def test_swing_buy_logs_self_learn_signal(monkeypatch):
    loop = _build_loop(prediction=100.2)
    loop._pred_id_by_symbol["TSLA"] = "pred-swing-1"
    calls: list[dict] = []

    def _fake_hook_on_signal(**kwargs):
        calls.append(kwargs)
        return "sig-swing-1"

    monkeypatch.setitem(sys.modules, "self_learn", SimpleNamespace(hook_on_signal=_fake_hook_on_signal))
    loop._execute = lambda *args, **kwargs: None  # type: ignore[assignment]
    latest = pd.DataFrame(
        [
            {"Date": pd.Timestamp("2026-03-13T09:58:00Z"), "Low": 99.5, "High": 101.0, "Close": 100.4, "RSI_14": 40.0, "MACD": -0.2, "MACD_SIGNAL": -0.1},
            {"Date": pd.Timestamp("2026-03-13T09:59:00Z"), "Low": 98.9, "High": 100.8, "Close": 100.0, "RSI_14": 25.0, "MACD": -0.05, "MACD_SIGNAL": -0.08},
        ]
    )

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=100.2,
        confidence=0.8,
        allow_long=True,
        latest_frame=latest,
    )

    assert calls == [{"action": "BUY", "prediction_id": "pred-swing-1", "entry_price": 100.0, "size": 81}]
    assert loop._signal_id_by_symbol["TSLA"] == "sig-swing-1"


def test_hk_kelly_zero_edge_bypass_is_controlled_by_feature_flag():
    loop = _build_loop(prediction=108.0)
    loop.config.prediction_thresholds = {"0700.HK": 0.01}
    loop.config.swing_strategy_enabled = False
    loop.config.hk_bypass_kelly_zero_edge = False
    loop.config.max_position_value = 100_000.0
    loop.account_value = 1_000_000.0
    loop.monte_carlo = _ZeroEdgeMonteCarlo()
    loop._kelly_sizer = _ZeroEdgeKellySizer()
    loop.market_buffers["0700.HK"] = pd.DataFrame(
        [
            {"Low": 98.0, "High": 101.0, "Close": 99.0, "RSI_14": 50.0},
            {"Low": 99.0, "High": 102.0, "Close": 100.0, "RSI_14": 50.0},
            {"Low": 100.0, "High": 103.0, "Close": 101.0, "RSI_14": 50.0},
        ]
    )
    executed: list[tuple] = []
    loop._execute = lambda *args, **kwargs: executed.append(args)  # type: ignore[assignment]

    loop.check_and_trade("0700.HK", current_price=100.0, prediction=108.0, confidence=0.8, allow_long=True)
    assert executed == []

    loop.config.hk_bypass_kelly_zero_edge = True
    loop.check_and_trade("0700.HK", current_price=100.0, prediction=108.0, confidence=0.8, allow_long=True)
    assert executed
    assert executed[-1][0:2] == ("0700.HK", "BUY")


def test_trade_logic_swing_sell_signal_triggers_exit():
    loop = _build_loop(prediction=100.1)
    loop.position_qty_by_symbol["TSLA"] = 12
    loop.highest_price_since_entry_by_symbol["TSLA"] = 100.0
    loop.cycles_since_buy_by_symbol["TSLA"] = 5
    executed: list[tuple[str, int, str]] = []

    def _fake_execute(symbol, side, qty, price, reason):
        executed.append((side, qty, reason))

    loop._execute = _fake_execute  # type: ignore[assignment]
    latest = pd.DataFrame(
        [
            {"Date": pd.Timestamp("2026-03-13T09:58:00Z"), "Low": 99.5, "High": 101.0, "Close": 100.2, "RSI_14": 62.0, "MACD": 0.2, "MACD_SIGNAL": 0.1},
            {"Date": pd.Timestamp("2026-03-13T09:59:00Z"), "Low": 99.7, "High": 100.3, "Close": 100.0, "RSI_14": 76.0, "MACD": 0.05, "MACD_SIGNAL": 0.08},
        ]
    )

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=100.1,
        confidence=0.2,
        allow_long=True,
        latest_frame=latest,
    )

    assert executed
    assert executed[0] == ("SELL", 12, "swing_signal")


def test_quick_take_profit_exits_at_one_percent_gain_before_other_signals():
    loop = _build_loop(prediction=100.1)
    loop.position_qty_by_symbol["TSLA"] = 10
    loop.entry_price_by_symbol["TSLA"] = 100.0
    loop.highest_price_since_entry_by_symbol["TSLA"] = 100.0
    loop.config.swing_strategy_enabled = False
    executed: list[tuple[str, int, str]] = []

    def _fake_execute(symbol, side, qty, price, reason):
        executed.append((side, qty, reason))

    loop._execute = _fake_execute  # type: ignore[assignment]

    loop.check_and_trade(
        symbol="TSLA",
        current_price=101.0,
        prediction=100.1,
        confidence=0.2,
        allow_long=True,
    )

    assert executed
    assert executed[0][0] == "SELL"
    assert "quick_take_profit_0.0100" == executed[0][2]


def test_max_hold_bars_forces_exit_when_position_is_stale():
    loop = _build_loop(prediction=100.1)
    loop.position_qty_by_symbol["TSLA"] = 6
    loop.entry_price_by_symbol["TSLA"] = 100.0
    loop.highest_price_since_entry_by_symbol["TSLA"] = 100.5
    loop.bars_held_by_symbol["TSLA"] = 4
    loop.config.max_hold_bars = 5
    loop.config.swing_strategy_enabled = False
    executed: list[tuple[str, int, str]] = []

    def _fake_execute(symbol, side, qty, price, reason):
        executed.append((side, qty, reason))

    loop._execute = _fake_execute  # type: ignore[assignment]

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.2,
        prediction=100.15,
        confidence=0.1,
        allow_long=True,
    )

    assert executed
    assert executed[0] == ("SELL", 6, "max_hold_5_bars")


def test_run_symbol_cycle_logs_symbol_prediction_line(monkeypatch):
    loop = _build_loop(prediction=102.5)
    messages: list[str] = []

    def _capture_info(msg, *args, **kwargs):
        rendered = msg % args if args else msg
        messages.append(str(rendered))

    loop.logger.info = _capture_info  # type: ignore[assignment]

    asyncio.run(loop._run_symbol_cycle("TSLA"))

    assert any(line.startswith("[TSLA] Prediction") for line in messages)


def test_model_threshold_uses_relative_change_for_entry():
    loop = _build_loop(prediction=100.4)
    executed: list[tuple[str, int, str]] = []

    def _fake_execute(symbol, side, qty, price, reason):
        executed.append((side, qty, reason))

    loop._execute = _fake_execute  # type: ignore[assignment]
    loop.config.swing_strategy_enabled = False

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=100.9,
        confidence=0.8,
        allow_long=True,
    )
    assert not executed

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=101.1,
        confidence=0.8,
        allow_long=True,
    )
    assert executed
    assert executed[0][0] == "BUY"


def test_model_entry_can_bypass_ror_gate_for_diagnostics():
    loop = _build_loop(prediction=101.8, allow_ror=False)
    executed: list[tuple[str, int, str]] = []

    def _fake_execute(symbol, side, qty, price, reason):
        executed.append((side, qty, reason))

    loop._execute = _fake_execute  # type: ignore[assignment]
    loop.config.swing_strategy_enabled = False

    loop.config.bypass_ror_gate = False
    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=101.8,
        confidence=0.8,
        allow_long=True,
    )
    assert not executed

    loop.config.bypass_ror_gate = True
    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=101.8,
        confidence=0.8,
        allow_long=True,
    )
    assert executed
    assert executed[-1][0] == "BUY"


def test_trade_check_logs_allow_long_and_qty_context():
    loop = _build_loop(prediction=101.2)
    loop.position_qty_by_symbol["TSLA"] = 7
    messages: list[str] = []

    def _capture_info(msg, *args, **kwargs):
        rendered = msg % args if args else msg
        messages.append(str(rendered))

    loop.logger.info = _capture_info  # type: ignore[assignment]
    loop.config.swing_strategy_enabled = False

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=99.5,
        confidence=0.4,
        allow_long=False,
    )

    assert any("TRADE_CHECK[TSLA] allow_long=False qty=7" in line for line in messages)
    assert any("DIAG_GATE[TSLA] allow_long=False qty=7" in line for line in messages)



def test_negative_qty_is_clamped_and_warned():
    loop = _build_loop(prediction=99.0)
    loop.position_qty_by_symbol["TSLA"] = -5
    infos: list[str] = []
    warnings: list[str] = []

    def _capture_info(msg, *args, **kwargs):
        infos.append(msg % args if args else msg)

    def _capture_warning(msg, *args, **kwargs):
        warnings.append(msg % args if args else msg)

    loop.logger.info = _capture_info  # type: ignore[assignment]
    loop.logger.warning = _capture_warning  # type: ignore[assignment]
    loop.config.swing_strategy_enabled = False

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=99.0,
        confidence=0.5,
        allow_long=True,
    )

    assert any("DIAG_QTY[TSLA] invalid negative qty=-5; clamped to 0" in line for line in warnings)
    assert any("TRADE_CHECK[TSLA] allow_long=True qty=0" in line for line in infos)


def test_bypass_ror_gate_emits_warning_log():
    loop = _build_loop(prediction=101.8, allow_ror=False)
    warnings: list[str] = []

    def _capture_warning(msg, *args, **kwargs):
        warnings.append(msg % args if args else msg)

    loop.logger.warning = _capture_warning  # type: ignore[assignment]
    loop.config.swing_strategy_enabled = False
    loop.config.bypass_ror_gate = True

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=101.8,
        confidence=0.8,
        allow_long=True,
    )

    assert any("[ROR_GATE][TSLA] bypass enabled for diagnostics" in line for line in warnings)


def test_high_confidence_bullish_pattern_relaxes_buy_threshold():
    loop = _build_loop(prediction=100.95)
    executed: list[tuple[str, int, str]] = []

    def _fake_execute(symbol, side, qty, price, reason):
        executed.append((side, qty, reason))

    loop._execute = _fake_execute  # type: ignore[assignment]
    loop.config.swing_strategy_enabled = False

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=100.95,
        confidence=0.6,
        allow_long=True,
        pattern_label="DoubleBottom",
        pattern_confidence=0.9,
    )

    assert executed
    assert executed[0][0] == "BUY"


def test_run_symbol_cycle_backwards_compatible_without_predict_pattern():
    model_manager = _DummyModelManagerNoPattern(prediction=102.0)
    cfg = LiveConfig(
        symbol="TSLA",
        symbols_list=["TSLA"],
        prediction_threshold=0.01,
        prediction_thresholds={"TSLA": 0.01},
        auto_trade=False,
        paper_trading=True,
        log_trade_decisions=True,
        polling_seconds=1,
    )
    loop = LiveTradingLoop(
        model_manager=model_manager,
        risk_controller=_DummyRiskController(allow_ror=True),
        futu_connector=_DummyConnector(),
        feature_generator=_PassFeatureGenerator(),
        config=cfg,
    )
    loop.alpha_engine = _PassAlphaEngine()
    loop.market_buffers["TSLA"] = pd.DataFrame([
        {"Date": pd.Timestamp("2026-03-13T09:59:00Z"), "Open": 99.0, "High": 100.0, "Low": 98.0, "Close": 99.5, "Volume": 9000, "data_source": "seed"},
        {"Date": pd.Timestamp("2026-03-13T09:59:30Z"), "Open": 99.5, "High": 100.1, "Low": 99.1, "Close": 100.0, "Volume": 9200, "data_source": "seed"},
    ])

    asyncio.run(loop._run_symbol_cycle("TSLA"))

    assert "TSLA" in loop.latest_prices


def test_run_symbol_cycle_handles_non_dict_pattern_payload():
    model_manager = _DummyModelManagerBadPatternPayload(prediction=102.0)
    cfg = LiveConfig(
        symbol="TSLA",
        symbols_list=["TSLA"],
        prediction_threshold=0.01,
        prediction_thresholds={"TSLA": 0.01},
        auto_trade=False,
        paper_trading=True,
        log_trade_decisions=True,
        polling_seconds=1,
    )
    loop = LiveTradingLoop(
        model_manager=model_manager,
        risk_controller=_DummyRiskController(allow_ror=True),
        futu_connector=_DummyConnector(),
        feature_generator=_PassFeatureGenerator(),
        config=cfg,
    )
    loop.alpha_engine = _PassAlphaEngine()
    loop.market_buffers["TSLA"] = pd.DataFrame([
        {"Date": pd.Timestamp("2026-03-13T09:59:00Z"), "Open": 99.0, "High": 100.0, "Low": 98.0, "Close": 99.5, "Volume": 9000, "data_source": "seed"},
        {"Date": pd.Timestamp("2026-03-13T09:59:30Z"), "Open": 99.5, "High": 100.1, "Low": 99.1, "Close": 100.0, "Volume": 9200, "data_source": "seed"},
    ])

    asyncio.run(loop._run_symbol_cycle("TSLA"))

    assert "TSLA" in loop.latest_prices


def test_run_symbol_cycle_handles_invalid_pattern_confidence_payload():
    model_manager = _DummyModelManagerBadPatternConfidence(prediction=102.0)
    cfg = LiveConfig(
        symbol="TSLA",
        symbols_list=["TSLA"],
        prediction_threshold=0.01,
        prediction_thresholds={"TSLA": 0.01},
        auto_trade=False,
        paper_trading=True,
        log_trade_decisions=True,
        polling_seconds=1,
    )
    loop = LiveTradingLoop(
        model_manager=model_manager,
        risk_controller=_DummyRiskController(allow_ror=True),
        futu_connector=_DummyConnector(),
        feature_generator=_PassFeatureGenerator(),
        config=cfg,
    )
    loop.alpha_engine = _PassAlphaEngine()
    captured: list[str] = []

    def _capture_warning(msg, *args, **kwargs):
        captured.append(msg % args if args else msg)

    loop.logger.warning = _capture_warning  # type: ignore[assignment]
    loop.market_buffers["TSLA"] = pd.DataFrame([
        {"Date": pd.Timestamp("2026-03-13T09:59:00Z"), "Open": 99.0, "High": 100.0, "Low": 98.0, "Close": 99.5, "Volume": 9000, "data_source": "seed"},
        {"Date": pd.Timestamp("2026-03-13T09:59:30Z"), "Open": 99.5, "High": 100.1, "Low": 99.1, "Close": 100.0, "Volume": 9200, "data_source": "seed"},
    ])

    asyncio.run(loop._run_symbol_cycle("TSLA"))

    assert any("predict_pattern confidence is invalid" in line for line in captured)


def test_stop_loss_exits_at_two_percent_drawdown():
    loop = _build_loop(prediction=100.1)
    loop.position_qty_by_symbol["TSLA"] = 8
    loop.entry_price_by_symbol["TSLA"] = 100.0
    loop.highest_price_since_entry_by_symbol["TSLA"] = 98.0
    loop.config.swing_strategy_enabled = False
    executed: list[tuple[str, int, str]] = []

    def _fake_execute(symbol, side, qty, price, reason):
        executed.append((side, qty, reason))

    loop._execute = _fake_execute  # type: ignore[assignment]

    loop.check_and_trade(
        symbol="TSLA",
        current_price=98.0,
        prediction=100.1,
        confidence=0.2,
        allow_long=True,
    )

    assert executed
    assert executed[0] == ("SELL", 8, "stop_loss_0.0200")


def test_position_cap_blocks_additional_model_entry():
    loop = _build_loop(prediction=101.5)
    loop.config.swing_strategy_enabled = False
    loop.config.max_positions = 1
    loop.position_qty_by_symbol["TSLA"] = 0
    loop.position_qty_by_symbol["TSLL"] = 3
    executed: list[tuple[str, int, str]] = []

    def _fake_execute(symbol, side, qty, price, reason):
        executed.append((side, qty, reason))

    loop._execute = _fake_execute  # type: ignore[assignment]

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=101.5,
        confidence=0.8,
        allow_long=True,
    )

    assert not executed


def test_daily_loss_gate_blocks_model_buy_before_execution():
    loop = _build_loop(prediction=101.8, allow_daily=False)
    loop.config.swing_strategy_enabled = False
    warnings: list[str] = []
    executed: list[tuple[str, int, str]] = []

    def _capture_warning(msg, *args, **kwargs):
        warnings.append(msg % args if args else msg)

    def _fake_execute(symbol, side, qty, price, reason):
        executed.append((side, qty, reason))

    loop.logger.warning = _capture_warning  # type: ignore[assignment]
    loop._execute = _fake_execute  # type: ignore[assignment]

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=101.8,
        confidence=0.8,
        allow_long=True,
    )

    assert not executed
    assert any("[DAILY_LOSS_GATE][TSLA] blocked BUY" in line for line in warnings)


def test_daily_loss_gate_receives_equity_arguments():
    loop = _build_loop(prediction=101.8)
    strict_risk = _StrictDailyLossRiskController()
    loop.risk_controller = strict_risk
    loop.config.swing_strategy_enabled = False
    loop.day_start_equity = 98_000.0
    loop.day_start_key = loop._current_trading_day_key()
    loop.account_value = 97_500.0
    executed: list[tuple[str, int, str]] = []

    def _fake_execute(symbol, side, qty, price, reason):
        executed.append((side, qty, reason))

    loop._execute = _fake_execute  # type: ignore[assignment]

    loop.check_and_trade(
        symbol="TSLA",
        current_price=100.0,
        prediction=101.8,
        confidence=0.8,
        allow_long=True,
    )

    assert strict_risk.daily_loss_args == {
        "day_start_equity": 98_000.0,
        "current_equity": 97_500.0,
    }
    assert executed


def test_reconnect_stops_after_three_failures_and_enables_offline_mode():
    class _FailReconnectConnector(_DummyConnector):
        def __init__(self):
            self.reconnect_calls = 0

        def reconnect(self, **_kwargs):
            self.reconnect_calls += 1
            raise RuntimeError("RECONNECT_BUDGET_EXCEEDED")

    connector = _FailReconnectConnector()
    loop = LiveTradingLoop(
        model_manager=_DummyModelManager(prediction=101.0),
        risk_controller=_DummyRiskController(allow_ror=True),
        futu_connector=connector,
        feature_generator=_PassFeatureGenerator(),
        config=LiveConfig(symbol="TSLA", symbols_list=["TSLA"]),
    )

    for _ in range(5):
        loop._attempt_reconnect()

    assert loop.futu_reconnect_failures == 3
    assert loop.broker_offline_fallback_mode is True
    assert connector.reconnect_calls == 3
def test_run_cycle_uses_data_manager_fallback_when_broker_is_down():
    fallback_dm = _DummyDataManager({"price": 123.45, "source": "INFOWAY"})
    loop = _build_loop(
        prediction=124.0,
        futu_connector=_DummyFailingConnector(),
        data_manager=fallback_dm,
    )
    captured: list[str] = []

    def _capture_info(msg, *args, **kwargs):
        captured.append(msg % args if args else msg)

    loop.logger.info = _capture_info  # type: ignore[assignment]
    loop._check_heartbeat()

    asyncio.run(loop._run_symbol_cycle("TSLA"))

    latest_row = loop.market_buffers["TSLA"].iloc[-1]
    assert latest_row["Open"] == 123.45
    assert latest_row["High"] == 123.45
    assert latest_row["Low"] == 123.45
    assert latest_row["Close"] == 123.45
    assert latest_row["Volume"] == 0.0
    assert latest_row["data_source"] == "INFOWAY"
    assert any("QuoteSource[TSLA] broker_online=False source=INFOWAY" in line for line in captured)
