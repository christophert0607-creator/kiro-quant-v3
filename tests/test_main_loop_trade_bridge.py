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


class _DummyRiskController:
    def circuit_breaker_triggered(self, *_args, **_kwargs):
        return False

    def allow_trade_with_ror(self, *_args, **_kwargs):
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


class _PassFeatureGenerator:
    def generate(self, frame):
        return frame


class _PassAlphaEngine:
    def select_features(self, _symbol, featured):
        return featured


def _build_loop(prediction: float, auto_trade: bool = False):
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
        risk_controller=_DummyRiskController(),
        futu_connector=_DummyConnector(),
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
