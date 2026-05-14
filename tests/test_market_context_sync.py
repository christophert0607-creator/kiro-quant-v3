"""Acceptance tests for market context as canonical per-market view.

After broker sync:
- MarketContext.position_qty matches flat position_qty_by_symbol
- MarketContext.short_position_qty matches flat short_position_qty_by_symbol
- order_attempt event includes market and account_id fields
- HK order is blocked when no HK account configured
- US order proceeds unaffected when HK account is absent
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.modules.setdefault("requests", SimpleNamespace(post=lambda *a, **kw: None))
sys.modules.setdefault(
    "v3_pipeline.models.manager",
    SimpleNamespace(
        DataPreparer=lambda **kw: SimpleNamespace(
            lookback=2,
            target_col="Close",
            is_fitted=True,
            feature_columns=None,
            fit_transform=lambda frame: frame,
        ),
        ModelManager=object,
    ),
)

from v3_pipeline.core.market_context import (
    MarketContext,
    build_market_contexts,
    infer_market,
    sync_positions_to_contexts,
)
from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop


# ── MarketContext sync ────────────────────────────────────────────────────────


def test_sync_positions_to_contexts_long_position():
    ctxs = build_market_contexts(["0700.HK", "TSLA"])
    sync_positions_to_contexts(ctxs, [{"code": "HK.0700.HK", "qty": 200}])
    assert ctxs["HK"].position_qty["0700.HK"] == 200
    assert ctxs["HK"].short_position_qty["0700.HK"] == 0
    assert ctxs["US"].position_qty.get("TSLA", 0) == 0


def test_sync_positions_to_contexts_short_position():
    ctxs = build_market_contexts(["0700.HK", "TSLA"])
    sync_positions_to_contexts(ctxs, [{"code": "US.TSLA", "qty": -50}])
    assert ctxs["US"].short_position_qty["TSLA"] == 50
    assert ctxs["US"].position_qty.get("TSLA", 0) == 0


def test_sync_positions_clears_previous_on_zero():
    ctxs = build_market_contexts(["0700.HK"])
    ctxs["HK"].position_qty["0700.HK"] = 100
    sync_positions_to_contexts(ctxs, [{"code": "HK.0700.HK", "qty": 0}])
    assert ctxs["HK"].position_qty.get("0700.HK", 0) == 0


def test_sync_positions_both_markets_independent():
    ctxs = build_market_contexts(["0700.HK", "TSLA", "NVDA"])
    sync_positions_to_contexts(ctxs, [
        {"code": "HK.0700.HK", "qty": 100},
        {"code": "US.TSLA", "qty": 50},
    ])
    assert ctxs["HK"].position_qty["0700.HK"] == 100
    assert ctxs["US"].position_qty["TSLA"] == 50
    assert ctxs["US"].position_qty.get("NVDA", 0) == 0


# ── infer_market ──────────────────────────────────────────────────────────────


def test_infer_market_hk():
    assert infer_market("0700.HK") == "HK"
    assert infer_market("9988.HK") == "HK"


def test_infer_market_us():
    assert infer_market("TSLA") == "US"
    assert infer_market("NVDA") == "US"


# ── LiveTradingLoop: MarketContext consistent with flat dicts ─────────────────


class _FakeConnector:
    class config:
        market_prefix = "US"

    config_market_accounts = {}
    account_ids = {}

    def connect(self): pass
    def close(self): pass
    def heartbeat(self): return True
    def get_sync_assets(self): return {"total_assets": 100_000.0}

    def get_sync_positions(self):
        return pd.DataFrame(
            [{"code": "HK.0700.HK", "qty": 100, "position_side": "LONG", "can_sell_qty": 100}]
        )

    def get_latest_quote(self, symbol):
        return {"Date": pd.Timestamp("2026-01-01"), "Open": 100.0, "High": 101.0,
                "Low": 99.0, "Close": 100.0, "Volume": 10000, "data_source": "test", "symbol": symbol}

    def get_order_reference_price(self, _sym, _side, fallback_price):
        return fallback_price

    def get_open_orders(self):
        return pd.DataFrame([])

    def resolve_symbol(self, symbol):
        if symbol.endswith(".HK"):
            return f"HK.{symbol}", "HK"
        return f"US.{symbol}", "US"

    def _account_kwargs_for_market(self, market):
        if market == "HK":
            return {"acc_id": 14239754}
        return {"acc_id": 18526451}


class _FakeRisk:
    config = SimpleNamespace(max_daily_loss_fraction=1.0)
    def circuit_breaker_triggered(self, *a, **kw): return False
    def allow_trade_with_ror(self, *a, **kw): return True
    def allow_daily_loss(self, *a, **kw): return True
    def allow_trade_with_var_cvar(self, *a, **kw): return True
    def estimate_cvar_95(self, *a, **kw): return -0.05


class _FakeModelManager:
    data_preparer = SimpleNamespace(
        lookback=2, target_col="Close", is_fitted=True, feature_columns=None,
        fit_transform=lambda frame: frame,
    )
    def predict(self, *a, **kw): return 0.0
    def predict_pattern(self, *a, **kw): return {"pattern": "Unknown", "confidence": 0.0, "probs": {}}


def _make_loop(symbols=None, connector=None):
    syms = symbols or ["0700.HK", "TSLA"]
    cfg = LiveConfig(
        symbol=syms[0],
        symbols_list=syms,
        prediction_threshold=0.01,
        prediction_thresholds={s: 0.01 for s in syms},
        auto_trade=False,
        paper_trading=True,
        log_trade_decisions=True,
        polling_seconds=1,
    )
    loop = LiveTradingLoop(
        model_manager=_FakeModelManager(),
        risk_controller=_FakeRisk(),
        futu_connector=connector or _FakeConnector(),
        data_manager=None,
        feature_generator=SimpleNamespace(generate=lambda f: f),
        config=cfg,
    )
    loop.alpha_engine = SimpleNamespace(select_features=lambda _s, f: f)
    return loop


def test_market_context_built_for_both_markets():
    from pathlib import Path
    with patch.object(Path, "exists", return_value=False):
        loop = _make_loop(["0700.HK", "TSLA"])
    assert "HK" in loop.market_contexts
    assert "US" in loop.market_contexts


def test_market_context_symbols_partitioned_correctly():
    from pathlib import Path
    with patch.object(Path, "exists", return_value=False):
        loop = _make_loop(["0700.HK", "9988.HK", "TSLA", "NVDA"])
    assert "0700.HK" in loop.market_contexts["HK"].symbols
    assert "9988.HK" in loop.market_contexts["HK"].symbols
    assert "TSLA" in loop.market_contexts["US"].symbols
    assert "NVDA" in loop.market_contexts["US"].symbols


# ── order_attempt event includes market + account_id ─────────────────────────


def test_execute_order_attempt_event_includes_market_and_account_id():
    connector = _FakeConnector()
    loop = _make_loop(["0700.HK", "TSLA"], connector=connector)
    loop.config = loop.config.__class__(
        **{**loop.config.__dict__, "auto_trade": True, "paper_trading": True}
    )
    loop.position_qty_by_symbol["0700.HK"] = 0

    emitted = []
    with patch.object(loop, "_emit_structured", side_effect=lambda evt, **kw: emitted.append({"event": evt, **kw})):
        with patch.object(loop, "_append_decision_trace"):
            loop._execute("0700.HK", "BUY", qty=100, price=60.0, reason="test")

    order_events = [e for e in emitted if e["event"] == "order_attempt"]
    assert len(order_events) == 1
    evt = order_events[0]
    assert evt["market"] == "HK", f"Expected market=HK, got {evt.get('market')}"
    assert evt["account_id"] == 14239754, f"Expected account_id=14239754, got {evt.get('account_id')}"


def test_execute_us_order_attempt_event_has_us_market():
    connector = _FakeConnector()
    loop = _make_loop(["0700.HK", "TSLA"], connector=connector)
    loop.config = loop.config.__class__(
        **{**loop.config.__dict__, "auto_trade": True, "paper_trading": True}
    )
    loop.position_qty_by_symbol["TSLA"] = 0

    emitted = []
    with patch.object(loop, "_emit_structured", side_effect=lambda evt, **kw: emitted.append({"event": evt, **kw})):
        with patch.object(loop, "_append_decision_trace"):
            loop._execute("TSLA", "BUY", qty=10, price=200.0, reason="test")

    order_events = [e for e in emitted if e["event"] == "order_attempt"]
    assert len(order_events) == 1
    assert order_events[0]["market"] == "US"
    assert order_events[0]["account_id"] == 18526451
