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
