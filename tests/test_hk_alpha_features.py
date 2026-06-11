import numpy as np
import pandas as pd
import pytest

from v3_pipeline.models.hk_alpha_features import (
    FEATURE_ORDER,
    build_hk_alpha_features,
)


def _make_frame(n: int = 60, intraday: bool = True, start_price: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    if intraday:
        # 2026-06-10 is a Wednesday; HK morning session bars at 60s.
        dates = pd.date_range("2026-06-10 09:30", periods=n, freq="1min")
    else:
        dates = pd.date_range("2026-01-05", periods=n, freq="B")
    close = start_price * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.0005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0005, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = rng.integers(1_000, 50_000, n).astype(float)
    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        }
    )
    # Standard indicator columns the live feature generator produces.
    df["RSI_14"] = 55.0
    df["MACD_HIST"] = 0.02
    df["ATR_14"] = close * 0.005
    df["BB_UPPER"] = close * 1.01
    df["BB_LOWER"] = close * 0.99
    df["SMA_5"] = close
    df["SMA_20"] = close
    return df


def test_feature_order_is_deterministic_and_complete():
    result = build_hk_alpha_features(_make_frame())
    assert list(result.frame.columns) == FEATURE_ORDER
    assert result.feature_names == FEATURE_ORDER
    assert len(result.frame) == 60


def test_no_nan_or_inf_in_output():
    df = _make_frame()
    # Poison some inputs.
    df.loc[5, "Close"] = np.nan
    df.loc[10, "Volume"] = np.inf
    df.loc[15, "RSI_14"] = np.nan
    result = build_hk_alpha_features(df)
    values = result.frame.to_numpy()
    assert np.isfinite(values).all()


def test_missing_context_yields_zero_features_and_false_flags():
    result = build_hk_alpha_features(_make_frame(), context=None)
    assert result.source_flags["context_available"] is False
    assert result.source_flags["gap_available"] is False
    assert (result.frame["mom_2800"] == 0.0).all()
    assert (result.frame["gap_open"] == 0.0).all()
    assert (result.frame["flag_context"] == 0.0).all()


def test_context_features_are_broadcast_and_flagged():
    ctx = {
        "mom_2800": 0.004,
        "mom_3033": -0.002,
        "us_overnight": 0.01,
        "posture_risk_on": 1.0,
        "prev_close": 99.0,
        "lstm_pred_move": 0.003,
        "symbol_te": 0.0005,
    }
    df = _make_frame()
    result = build_hk_alpha_features(df, context=ctx)
    assert result.source_flags["context_available"] is True
    assert result.source_flags["gap_available"] is True
    assert np.allclose(result.frame["mom_2800"], 0.004)
    expected_gap = float(df["Open"].iloc[0]) / 99.0 - 1.0
    assert result.frame["gap_open"].iloc[0] == pytest.approx(expected_gap)
    assert (result.frame["flag_context"] == 1.0).all()
    assert np.allclose(result.frame["lstm_pred_move"], 0.003)


def test_session_features_intraday():
    # Bars spanning pre-lunch boundary: 11:25 .. 12:04 (Wednesday).
    df = _make_frame(40)
    df["Date"] = pd.date_range("2026-06-10 11:25", periods=40, freq="1min")
    result = build_hk_alpha_features(df)
    frame = result.frame
    assert result.source_flags["time_available"] is True
    # 11:30-11:59 bars are pre-lunch.
    minutes = pd.to_datetime(df["Date"]).dt.hour * 60 + pd.to_datetime(df["Date"]).dt.minute
    pre_lunch_rows = ((minutes >= 11 * 60 + 30) & (minutes < 12 * 60)).to_numpy()
    assert (frame["is_pre_lunch"].to_numpy() == pre_lunch_rows.astype(float)).all()
    # Wednesday one-hot.
    assert (frame["dow_wed"] == 1.0).all()
    assert (frame["dow_mon"] == 0.0).all()
    # min_since_open within [0, 1].
    assert frame["min_since_open"].between(0.0, 1.0).all()


def test_daily_bars_disable_time_features():
    result = build_hk_alpha_features(_make_frame(intraday=False))
    assert result.source_flags["time_available"] is False
    assert (result.frame["min_since_open"] == 0.0).all()
    assert (result.frame["flag_time"] == 0.0).all()


def test_missing_indicator_columns_degrade_to_neutral():
    df = _make_frame()
    df = df.drop(columns=["RSI_14", "MACD_HIST", "ATR_14", "BB_UPPER", "BB_LOWER", "SMA_5", "SMA_20"])
    result = build_hk_alpha_features(df)
    assert np.allclose(result.frame["rsi_14"], 0.5)
    assert np.allclose(result.frame["bb_position"], 0.5)
    assert np.isfinite(result.frame.to_numpy()).all()


def test_missing_required_column_raises():
    df = _make_frame().drop(columns=["Volume"])
    with pytest.raises(ValueError, match="Volume"):
        build_hk_alpha_features(df)


def test_empty_frame_raises():
    with pytest.raises(ValueError, match="empty"):
        build_hk_alpha_features(_make_frame().iloc[0:0])
