import pandas as pd

from v3_pipeline.features.indicators import REQUIRED_COLUMNS, TechnicalIndicatorGenerator


def _build_ohlcv(close_values, volume_values):
    n = len(close_values)
    return pd.DataFrame(
        {
            "Date": pd.date_range("2024-01-01", periods=n, freq="min"),
            "Open": close_values,
            "High": close_values,
            "Low": close_values,
            "Close": close_values,
            "Volume": volume_values,
        }
    )


def _assert_no_all_nan_indicator_columns(df: pd.DataFrame) -> None:
    indicator_columns = [c for c in df.columns if c not in REQUIRED_COLUMNS]
    assert indicator_columns, "expected fallback to generate indicator columns"
    assert all(not df[col].isna().all() for col in indicator_columns)


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
