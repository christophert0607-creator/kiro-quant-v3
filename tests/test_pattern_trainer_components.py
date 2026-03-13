import pandas as pd
import pytest

torch = pytest.importorskip("torch")

from v3_pipeline.models.brain import StockPatternModel
from v3_pipeline.models.trainer_pattern_v1 import PatternWindowDataset, generate_pattern_label


def _sample_frame(rows: int = 140) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=rows, freq="D")
    close = pd.Series(range(rows), dtype=float) + 100.0
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": close - 1,
            "High": close + 1,
            "Low": close - 2,
            "Close": close,
            "Volume": 1_000_000,
            "MA_20": close.rolling(20, min_periods=1).mean(),
            "MA_50": close.rolling(50, min_periods=1).mean(),
            "RSI_14": 55.0,
            "BB_upper": close + 2,
            "BB_lower": close - 2,
        }
    )


def test_pattern_label_is_valid_class_id() -> None:
    frame = _sample_frame(80)
    label = generate_pattern_label(frame.tail(60))
    assert isinstance(label, int)
    assert 0 <= label <= 7


def test_pattern_dataset_and_model_forward_shapes() -> None:
    frame = _sample_frame(130)
    ds = PatternWindowDataset(frame, lookback=60)
    x, y_reg, y_cls = ds[0]

    assert x.shape == (60, len(ds.feature_cols))
    assert y_reg.shape == (1,)
    assert y_cls.ndim == 0

    model = StockPatternModel(input_dim=len(ds.feature_cols), num_patterns=8)
    price_out, pattern_out = model(x.unsqueeze(0))
    assert price_out.shape == (1, 1)
    assert pattern_out.shape == (1, 8)
    assert torch.isfinite(price_out).all()
