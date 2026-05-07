import pytest
import pandas as pd
import numpy as np

from v3_pipeline.features.indicators import (
    FALLBACK_ALL_NAN_DEFAULTS,
    REQUIRED_COLUMNS,
    TechnicalIndicatorGenerator,
)

# Expected indicator columns produced by the fallback path.
EXPECTED_INDICATOR_COLUMNS = list(FALLBACK_ALL_NAN_DEFAULTS.keys())


def _build_ohlcv(close_values, volume_values, high_values=None, low_values=None):
    n = len(close_values)
    return pd.DataFrame(
        {
            "Date": pd.date_range("2024-01-01", periods=n, freq="min"),
            "Open": close_values,
            "High": high_values if high_values is not None else close_values,
            "Low": low_values if low_values is not None else close_values,
            "Close": close_values,
            "Volume": volume_values,
        }
    )


def _assert_no_all_nan_indicator_columns(df: pd.DataFrame) -> None:
    indicator_columns = [c for c in df.columns if c not in REQUIRED_COLUMNS]
    assert indicator_columns, "expected fallback to generate indicator columns"
    assert all(not df[col].isna().all() for col in indicator_columns)


# ── Basic fallback path ───────────────────────────────────────────────────────

def test_fallback_flat_close_series_has_no_all_nan_indicators(monkeypatch):
    generator = TechnicalIndicatorGenerator()
    monkeypatch.setattr(generator, "use_talib", False)

    source = _build_ohlcv(close_values=[100.0] * 60, volume_values=[1000.0] * 60)
    featured = generator.generate(source)

    _assert_no_all_nan_indicator_columns(featured)


def test_fallback_increasing_close_series_has_no_all_nan_indicators(monkeypatch):
    generator = TechnicalIndicatorGenerator()
    monkeypatch.setattr(generator, "use_talib", False)

    close_values = [100.0 + i for i in range(60)]
    source = _build_ohlcv(close_values=close_values, volume_values=[1000.0] * 60)
    featured = generator.generate(source)

    _assert_no_all_nan_indicator_columns(featured)


def test_fallback_zero_volume_series_has_no_all_nan_indicators(monkeypatch):
    generator = TechnicalIndicatorGenerator()
    monkeypatch.setattr(generator, "use_talib", False)

    close_values = [100.0 + (i % 3) for i in range(60)]
    source = _build_ohlcv(close_values=close_values, volume_values=[0.0] * 60)
    featured = generator.generate(source)

    _assert_no_all_nan_indicator_columns(featured)


# ── Short history edge cases ──────────────────────────────────────────────────

def test_fallback_short_history_5_rows_does_not_raise(monkeypatch):
    """5 rows is less than all indicator windows; fallback must not raise."""
    generator = TechnicalIndicatorGenerator()
    monkeypatch.setattr(generator, "use_talib", False)

    source = _build_ohlcv(close_values=[100.0] * 5, volume_values=[1000.0] * 5)
    featured = generator.generate(source)

    indicator_columns = [c for c in featured.columns if c not in REQUIRED_COLUMNS]
    assert indicator_columns, "expected indicator columns even for short history"


def test_fallback_short_history_1_row_does_not_raise(monkeypatch):
    """Single-row input: all indicators will be NaN but fallback fills defaults."""
    generator = TechnicalIndicatorGenerator()
    monkeypatch.setattr(generator, "use_talib", False)

    source = _build_ohlcv(close_values=[150.0], volume_values=[500.0])
    featured = generator.generate(source)

    indicator_columns = [c for c in featured.columns if c not in REQUIRED_COLUMNS]
    assert len(indicator_columns) > 0


def test_fallback_short_history_result_has_no_all_nan_columns(monkeypatch):
    """All-NaN columns caused by short history should be filled with neutral defaults."""
    generator = TechnicalIndicatorGenerator()
    monkeypatch.setattr(generator, "use_talib", False)

    source = _build_ohlcv(close_values=[100.0] * 10, volume_values=[1000.0] * 10)
    featured = generator.generate(source)

    _assert_no_all_nan_indicator_columns(featured)


# ── Column completeness ───────────────────────────────────────────────────────

def test_fallback_produces_all_expected_indicator_columns(monkeypatch):
    """Every indicator in FALLBACK_ALL_NAN_DEFAULTS should appear in output."""
    generator = TechnicalIndicatorGenerator()
    monkeypatch.setattr(generator, "use_talib", False)

    source = _build_ohlcv(close_values=[100.0 + i for i in range(60)], volume_values=[1000.0] * 60)
    featured = generator.generate(source)

    for col in EXPECTED_INDICATOR_COLUMNS:
        assert col in featured.columns, f"Missing expected indicator column: {col}"


def test_fallback_output_contains_required_ohlcv_columns(monkeypatch):
    """Original OHLCV columns must be preserved in the output DataFrame."""
    generator = TechnicalIndicatorGenerator()
    monkeypatch.setattr(generator, "use_talib", False)

    source = _build_ohlcv(close_values=[100.0] * 60, volume_values=[1000.0] * 60)
    featured = generator.generate(source)

    for col in REQUIRED_COLUMNS:
        assert col in featured.columns, f"REQUIRED_COLUMNS entry missing from output: {col}"


# ── All-NaN / inf handling ────────────────────────────────────────────────────

def test_fallback_handles_constant_high_low_equal_to_close(monkeypatch):
    """When H/L/C are identical the WILLR denominator is zero; should not produce NaN columns."""
    generator = TechnicalIndicatorGenerator()
    monkeypatch.setattr(generator, "use_talib", False)

    close_values = [100.0] * 60
    source = _build_ohlcv(
        close_values=close_values,
        volume_values=[1000.0] * 60,
        high_values=close_values,
        low_values=close_values,
    )
    featured = generator.generate(source)

    _assert_no_all_nan_indicator_columns(featured)


def test_fallback_no_inf_values_in_indicator_columns(monkeypatch):
    """Indicator columns must not contain ±inf values."""
    generator = TechnicalIndicatorGenerator()
    monkeypatch.setattr(generator, "use_talib", False)

    close_values = [100.0] * 60
    source = _build_ohlcv(close_values=close_values, volume_values=[1000.0] * 60)
    featured = generator.generate(source)

    indicator_columns = [c for c in featured.columns if c not in REQUIRED_COLUMNS]
    for col in indicator_columns:
        vals = featured[col].values
        assert not np.any(np.isposinf(vals)), f"Column {col} contains +inf"
        assert not np.any(np.isneginf(vals)), f"Column {col} contains -inf"


def test_fallback_neutral_defaults_in_range(monkeypatch):
    """Neutral fill defaults should be within reasonable bounds (no extreme values)."""
    generator = TechnicalIndicatorGenerator()
    monkeypatch.setattr(generator, "use_talib", False)

    # Use 1-row input to force all-NaN and trigger neutral defaults
    source = _build_ohlcv(close_values=[100.0], volume_values=[0.0])
    featured = generator.generate(source)

    rsi = featured["RSI_14"].iloc[0]
    assert 0.0 <= rsi <= 100.0, f"RSI neutral default out of range: {rsi}"

    mfi = featured["MFI_14"].iloc[0]
    assert 0.0 <= mfi <= 100.0, f"MFI neutral default out of range: {mfi}"


# ── Optional parity test (only when TA-Lib is installed) ─────────────────────

try:
    import talib as _talib  # type: ignore
    _TALIB_AVAILABLE = True
except Exception:
    _TALIB_AVAILABLE = False


@pytest.mark.skipif(not _TALIB_AVAILABLE, reason="TA-Lib not installed in this environment")
def test_talib_and_fallback_produce_same_indicator_columns():
    """TA-Lib and pandas paths must produce the same set of indicator columns."""
    close_values = [100.0 + i * 0.5 for i in range(100)]
    source = _build_ohlcv(
        close_values=close_values,
        volume_values=[5000.0] * 100,
        high_values=[v + 1.0 for v in close_values],
        low_values=[v - 1.0 for v in close_values],
    )

    gen_talib = TechnicalIndicatorGenerator()
    gen_talib.use_talib = True

    gen_fallback = TechnicalIndicatorGenerator()
    gen_fallback.use_talib = False

    featured_talib = gen_talib.generate(source.copy())
    featured_fallback = gen_fallback.generate(source.copy())

    cols_talib = set(featured_talib.columns) - set(REQUIRED_COLUMNS)
    cols_fallback = set(featured_fallback.columns) - set(REQUIRED_COLUMNS)

    assert cols_talib == cols_fallback, (
        f"Column mismatch between TA-Lib and fallback.\n"
        f"Only in TA-Lib: {cols_talib - cols_fallback}\n"
        f"Only in fallback: {cols_fallback - cols_talib}"
    )
