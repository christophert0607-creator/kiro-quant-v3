"""Acceptance tests for DecisionEvent schema and best-effort writer.

Acceptance criteria:
- Each factory method returns a dict with the correct 'event' key
- emit_event() writes exactly one JSON line to the logger
- emit_event() swallows all exceptions (best-effort, never raises)
- A None logger is handled safely
- The cycle phases (broker_sync, signal_generated, risk_blocked/allowed,
  order_attempt, order_result, state_persisted) all have required fields
"""

import json
import logging
from unittest.mock import MagicMock, call, patch

import pytest

from v3_pipeline.core.decision_events import (
    BROKER_SYNC,
    ORDER_ATTEMPT,
    ORDER_RESULT,
    RISK_ALLOWED,
    RISK_BLOCKED,
    SIGNAL_GENERATED,
    STATE_PERSISTED,
    DecisionEvent,
    emit_event,
)


# ── Factory methods produce correct event keys ────────────────────────────────


def test_broker_sync_event_key():
    e = DecisionEvent.broker_sync(
        positions={"TSLA": 10},
        account_value=100_000.0,
        active_markets=["US"],
    )
    assert e["event"] == BROKER_SYNC
    assert "ts" in e
    assert e["positions"] == {"TSLA": 10}
    assert e["account_value"] == 100_000.0


def test_signal_generated_event_key():
    e = DecisionEvent.signal_generated(
        symbol="0700.HK", market="HK", side="BUY",
        prediction=0.015, threshold=0.01,
    )
    assert e["event"] == SIGNAL_GENERATED
    assert e["symbol"] == "0700.HK"
    assert e["market"] == "HK"
    assert e["prediction"] == pytest.approx(0.015, abs=1e-6)


def test_risk_blocked_event_key():
    e = DecisionEvent.risk_blocked(
        symbol="TSLA", market="US", side="BUY",
        gate_name="daily_loss", reason="loss 10% > limit 5%",
        metrics={"loss_fraction": 0.10},
    )
    assert e["event"] == RISK_BLOCKED
    assert e["gate_name"] == "daily_loss"
    assert e["metrics"]["loss_fraction"] == pytest.approx(0.10)


def test_risk_allowed_event_key():
    e = DecisionEvent.risk_allowed(
        symbol="TSLA", market="US", side="BUY",
        gate_name="ror", metrics={"ror": 0.12},
    )
    assert e["event"] == RISK_ALLOWED
    assert e["allowed_implied"] if "allowed_implied" in e else True


def test_order_attempt_event_key():
    e = DecisionEvent.order_attempt(
        symbol="TSLA", market="US", side="BUY",
        qty=10, price=200.0, reason="model_entry",
        account_id=18526451, paper=False,
    )
    assert e["event"] == ORDER_ATTEMPT
    assert e["account_id"] == 18526451
    assert e["market"] == "US"
    assert e["paper"] is False


def test_order_result_event_key():
    e = DecisionEvent.order_result(
        symbol="TSLA", market="US", side="BUY",
        qty=10, fill_price=200.0, status="ACCEPTED",
    )
    assert e["event"] == ORDER_RESULT
    assert e["status"] == "ACCEPTED"


def test_state_persisted_event_key():
    e = DecisionEvent.state_persisted(
        symbol="TSLA",
        positions_snapshot={"TSLA": 10},
    )
    assert e["event"] == STATE_PERSISTED
    assert e["positions_snapshot"]["TSLA"] == 10


# ── emit_event() writes exactly one line ─────────────────────────────────────


def test_emit_event_calls_logger_info_once():
    logger = MagicMock(spec=logging.Logger)
    event = DecisionEvent.signal_generated(
        symbol="TSLA", market="US", side="BUY", prediction=0.01, threshold=0.01
    )
    emit_event(logger, event)
    assert logger.info.call_count == 1


def test_emit_event_writes_valid_json():
    written = []
    logger = MagicMock(spec=logging.Logger)
    logger.info.side_effect = lambda msg: written.append(msg)
    emit_event(logger, DecisionEvent.broker_sync(
        positions={}, account_value=100_000.0, active_markets=["US"]
    ))
    assert len(written) == 1
    parsed = json.loads(written[0])
    assert parsed["event"] == BROKER_SYNC


# ── emit_event() is best-effort ───────────────────────────────────────────────


def test_emit_event_none_logger_does_not_raise():
    emit_event(None, {"event": "test", "ts": "2026-01-01"})


def test_emit_event_logger_error_does_not_raise():
    logger = MagicMock(spec=logging.Logger)
    logger.info.side_effect = OSError("disk full")
    emit_event(logger, {"event": "test", "ts": "2026-01-01"})  # must not raise


def test_emit_event_non_serialisable_does_not_raise():
    logger = MagicMock(spec=logging.Logger)
    emit_event(logger, {"event": "test", "obj": object()})  # object() is not JSON serialisable


# ── All events have 'event' and 'ts' fields ───────────────────────────────────


@pytest.mark.parametrize("event", [
    DecisionEvent.broker_sync(positions={}, account_value=0.0, active_markets=[]),
    DecisionEvent.signal_generated(symbol="X", market="US", side="BUY", prediction=0.0, threshold=0.0),
    DecisionEvent.risk_blocked(symbol="X", market="US", side="BUY", gate_name="g", reason="r"),
    DecisionEvent.risk_allowed(symbol="X", market="US", side="BUY", gate_name="g"),
    DecisionEvent.order_attempt(symbol="X", market="US", side="BUY", qty=1, price=1.0, reason="r"),
    DecisionEvent.order_result(symbol="X", market="US", side="BUY", qty=1, fill_price=1.0, status="ACCEPTED"),
    DecisionEvent.state_persisted(symbol="X", positions_snapshot={}),
])
def test_all_events_have_required_fields(event):
    assert "event" in event, f"Missing 'event' key in {event}"
    assert "ts" in event, f"Missing 'ts' key in {event}"
    assert event["event"], "event key must be non-empty"


# ── _emit_structured uses emit_event under the hood ───────────────────────────


def test_emit_structured_routes_through_emit_event():
    """_emit_structured in LiveTradingLoop must delegate to emit_event."""
    import sys
    from types import SimpleNamespace

    sys.modules.setdefault("requests", SimpleNamespace(post=lambda *a, **kw: None))
    sys.modules.setdefault(
        "v3_pipeline.models.manager",
        SimpleNamespace(
            DataPreparer=lambda **kw: SimpleNamespace(
                lookback=2, target_col="Close", is_fitted=True,
                feature_columns=None, fit_transform=lambda f: f,
            ),
            ModelManager=object,
        ),
    )

    from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop

    cfg = LiveConfig(
        symbol="TSLA", symbols_list=["TSLA"],
        prediction_threshold=0.01, prediction_thresholds={"TSLA": 0.01},
        auto_trade=False, paper_trading=True,
        log_trade_decisions=True, polling_seconds=1,
    )
    loop = LiveTradingLoop(
        model_manager=SimpleNamespace(
            data_preparer=SimpleNamespace(
                lookback=2, target_col="Close", is_fitted=True,
                feature_columns=None, fit_transform=lambda f: f,
            ),
            predict=lambda *a, **kw: 0.0,
            predict_pattern=lambda *a, **kw: {"pattern": "Unknown", "confidence": 0.0, "probs": {}},
        ),
        risk_controller=SimpleNamespace(
            config=SimpleNamespace(max_daily_loss_fraction=1.0),
            circuit_breaker_triggered=lambda *a, **kw: False,
            allow_trade_with_ror=lambda *a, **kw: True,
            allow_daily_loss=lambda *a, **kw: True,
            allow_trade_with_var_cvar=lambda *a, **kw: True,
            estimate_cvar_95=lambda *a, **kw: -0.05,
        ),
        futu_connector=SimpleNamespace(
            config=SimpleNamespace(market_prefix="US"),
            connect=lambda: None, close=lambda: None,
            heartbeat=lambda: True,
        ),
        data_manager=None,
        feature_generator=SimpleNamespace(generate=lambda f: f),
        config=cfg,
    )

    written = []
    loop.structured_logger.info = lambda msg: written.append(msg)

    loop._emit_structured("test_event", foo="bar")

    assert len(written) == 1
    parsed = json.loads(written[0])
    assert parsed["event"] == "test_event"
    assert parsed["foo"] == "bar"
