from unittest.mock import patch

from tests.test_live_execution_ordering import _FakeConnector, _make_loop


def test_low_confidence_swing_buy_suppressed():
    loop = _make_loop(
        auto_trade=True,
        paper_trading=False,
        connector=_FakeConnector(),
        swing_buy_min_confidence=0.45,
        model_buy_min_confidence=0.55,
        order_throttle_seconds=0,
    )
    loop.position_qty_by_symbol["TSLA"] = 0

    assert not loop._signal_density_gate_allows("TSLA", "swing", 0.44)


def test_low_confidence_swing_buy_suppressed_before_execution():
    loop = _make_loop(
        auto_trade=True,
        paper_trading=False,
        connector=_FakeConnector(),
        swing_buy_min_confidence=0.45,
        model_buy_min_confidence=0.55,
        order_throttle_seconds=0,
    )
    loop.position_qty_by_symbol["TSLA"] = 0

    with (
        patch.object(loop, "_evaluate_swing_signal", return_value={"buy_signal": True, "sell_signal": False}),
        patch.object(loop, "_execute") as execute,
    ):
        loop.check_and_trade(
            "TSLA",
            current_price=100.0,
            prediction=100.5,
            confidence=0.44,
            allow_long=True,
        )

    execute.assert_not_called()


def test_low_confidence_model_buy_suppressed():
    loop = _make_loop(
        auto_trade=True,
        paper_trading=False,
        connector=_FakeConnector(),
        swing_buy_min_confidence=0.45,
        model_buy_min_confidence=0.55,
        order_throttle_seconds=0,
    )
    loop.position_qty_by_symbol["TSLA"] = 0

    assert not loop._signal_density_gate_allows("TSLA", "model", 0.54)


def test_low_confidence_model_buy_suppressed_before_execution():
    loop = _make_loop(
        auto_trade=True,
        paper_trading=False,
        connector=_FakeConnector(),
        swing_buy_min_confidence=0.45,
        model_buy_min_confidence=0.55,
        order_throttle_seconds=0,
    )
    loop.position_qty_by_symbol["TSLA"] = 0

    with (
        patch.object(loop, "_evaluate_swing_signal", return_value={"buy_signal": False, "sell_signal": False}),
        patch.object(loop, "_execute") as execute,
    ):
        loop.check_and_trade(
            "TSLA",
            current_price=100.0,
            prediction=102.0,
            confidence=0.54,
            allow_long=True,
        )

    execute.assert_not_called()


def test_combined_signal_ranks_above_swing_only():
    loop = _make_loop(
        auto_trade=True,
        paper_trading=False,
        connector=_FakeConnector(),
        max_orders_per_cycle=1,
        order_throttle_seconds=0,
    )
    loop._collect_buy_candidates = True

    loop._submit_buy_candidate(
        "SWING",
        qty=1,
        price=100.0,
        reason="swing_signal_conf=0.700",
        confidence=0.70,
        predicted_move_pct=0.8,
        signal_type="swing_only",
    )
    loop._submit_buy_candidate(
        "BOTH",
        qty=1,
        price=100.0,
        reason="combined_signal_conf=0.700",
        confidence=0.70,
        predicted_move_pct=0.8,
        signal_type="both",
    )

    with (
        patch.object(loop, "_record_long_signal"),
        patch.object(loop, "_append_decision_trace"),
    ):
        loop._collect_buy_candidates = False
        loop._flush_buy_candidates()

    assert loop.futu_connector.place_order_calls == [("BOTH", 1, "BUY", 100.0)]


def test_signal_ranking_avoids_symbol_order_bias():
    connector = _FakeConnector()
    loop = _make_loop(
        auto_trade=True,
        paper_trading=False,
        connector=connector,
        max_orders_per_cycle=1,
        order_throttle_seconds=0,
    )
    loop._collect_buy_candidates = True

    loop._submit_buy_candidate(
        "AAA",
        qty=1,
        price=100.0,
        reason="model_signal_conf=0.560",
        confidence=0.56,
        predicted_move_pct=0.1,
        signal_type="model_only",
    )
    loop._submit_buy_candidate(
        "ZZZ",
        qty=1,
        price=100.0,
        reason="model_signal_conf=0.900",
        confidence=0.90,
        predicted_move_pct=1.2,
        signal_type="model_only",
    )

    with (
        patch.object(loop, "_record_long_signal"),
        patch.object(loop, "_append_decision_trace"),
    ):
        loop._collect_buy_candidates = False
        loop._flush_buy_candidates()

    assert connector.place_order_calls == [("ZZZ", 1, "BUY", 100.0)]


def test_sell_executed_in_cycle_defers_all_buy_candidates():
    connector = _FakeConnector()
    loop = _make_loop(
        auto_trade=True,
        paper_trading=False,
        connector=connector,
        max_orders_per_cycle=10,
        order_throttle_seconds=0,
    )
    loop._collect_buy_candidates = True
    loop.position_qty_by_symbol["TSLA"] = 1
    loop._submit_buy_candidate(
        "TXN",
        qty=1,
        price=100.0,
        reason="model_signal_conf=0.900",
        confidence=0.90,
        predicted_move_pct=1.0,
        signal_type="model_only",
    )
    loop._submit_buy_candidate(
        "AVGO",
        qty=1,
        price=100.0,
        reason="model_signal_conf=0.850",
        confidence=0.85,
        predicted_move_pct=1.0,
        signal_type="model_only",
    )

    with patch.object(loop, "_append_decision_trace"):
        loop._execute("TSLA", "SELL", qty=1, price=100.0, reason="model_signal")
        loop._collect_buy_candidates = False
        loop._flush_buy_candidates()

    assert connector.place_order_calls == [("TSLA", 1, "SELL", 100.0)]
    assert len(loop._deferred_buy_candidates) == 2
    assert loop._buy_candidates == []
    loop._reset_order_rate_limit_cycle()
    assert len(loop._buy_candidates) == 2


def test_throttle_active_defers_buy_queue_once_without_order_rate_limit_spam(monkeypatch):
    connector = _FakeConnector()
    loop = _make_loop(
        auto_trade=True,
        paper_trading=False,
        connector=connector,
        max_orders_per_cycle=10,
        order_throttle_seconds=30,
    )
    loop._collect_buy_candidates = True
    loop._last_order_monotonic = 1_000.0
    monkeypatch.setattr("v3_pipeline.core.main_loop.time.monotonic", lambda: 1_005.0)
    for symbol in ("TXN", "AVGO", "BAC"):
        loop._submit_buy_candidate(
            symbol,
            qty=1,
            price=100.0,
            reason="model_signal_conf=0.900",
            confidence=0.90,
            predicted_move_pct=1.0,
            signal_type="model_only",
        )

    with patch.object(loop.logger, "warning") as warn:
        loop._collect_buy_candidates = False
        loop._flush_buy_candidates()

    warning_text = "\n".join(str(call.args[0]) for call in warn.call_args_list)
    assert "[BUY_QUEUE_DEFERRED]" in warning_text
    assert "reason=order_throttle" in warning_text
    assert "[ORDER_RATE_LIMIT]" not in warning_text
    assert connector.place_order_calls == []
    assert len(loop._deferred_buy_candidates) == 3


def test_swing_buy_suppressed_when_model_sell_without_model_buy():
    loop = _make_loop(
        auto_trade=True,
        paper_trading=False,
        connector=_FakeConnector(),
        swing_buy_min_confidence=0.45,
        model_buy_min_confidence=0.55,
        order_throttle_seconds=0,
    )
    loop.position_qty_by_symbol["TSLA"] = 0

    with (
        patch.object(loop, "_evaluate_swing_signal", return_value={"buy_signal": True, "sell_signal": False}),
        patch.object(loop, "_submit_buy_candidate") as submit,
        patch.object(loop, "_execute") as execute,
    ):
        loop.check_and_trade(
            "TSLA",
            current_price=100.0,
            prediction=98.0,
            confidence=0.90,
            allow_long=True,
        )

    submit.assert_not_called()
    execute.assert_not_called()
