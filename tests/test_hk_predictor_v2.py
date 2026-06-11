import json
import pickle

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.isotonic import IsotonicRegression

from v3_pipeline.models.hk_alpha_features import FEATURE_ORDER
from v3_pipeline.models.hk_predictor_v2 import (
    HKPredictorV2,
    find_latest_artifact,
)


def _write_artifact(model_dir, stamp: str, guard_pass: bool = True, mean_ret: float = 0.003):
    n_feat = len(FEATURE_ORDER)
    X = np.zeros((4, n_feat))
    reg = DummyRegressor(strategy="constant", constant=mean_ret).fit(X, [mean_ret] * 4)
    clf = DummyClassifier(strategy="prior").fit(X, [0, 1, 1, 1])  # prob_up = 0.75
    calib = IsotonicRegression(out_of_bounds="clip").fit([0.0, 0.5, 1.0], [0.0, 0.5, 1.0])
    bundle = {
        "ret_head": reg,
        "prob_head": clf,
        "calibrator": calib,
        "feature_names": list(FEATURE_ORDER),
        "symbol_te": {"0700.HK": 0.001},
        "config": {"max_bars": 30},
        "backend": "test",
    }
    artifact = model_dir / f"hkalpha1_{stamp}.pkl"
    with open(artifact, "wb") as fh:
        pickle.dump(bundle, fh)
    sidecar = model_dir / f"hkalpha1_{stamp}.json"
    sidecar.write_text(json.dumps({"artifact": artifact.name, "guard_pass": guard_pass}))
    return artifact


def _live_frame(n: int = 70, price: float = 300.0) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    close = price * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    return pd.DataFrame(
        {
            "Date": pd.date_range("2026-06-10 09:30", periods=n, freq="1min"),
            "Open": close,
            "High": close * 1.001,
            "Low": close * 0.999,
            "Close": close,
            "Volume": rng.integers(1_000, 9_000, n).astype(float),
            "RSI_14": 55.0,
            "MACD_HIST": 0.01,
            "ATR_14": close * 0.005,
            "BB_UPPER": close * 1.01,
            "BB_LOWER": close * 0.99,
            "SMA_5": close,
            "SMA_20": close,
        }
    )


def test_no_artifact_raises(tmp_path):
    predictor = HKPredictorV2(model_dir=tmp_path)
    with pytest.raises(FileNotFoundError, match="hkalpha1"):
        predictor.load_latest()


def test_guard_failed_artifacts_are_ignored(tmp_path):
    _write_artifact(tmp_path, "20260610_120000", guard_pass=False)
    assert find_latest_artifact(tmp_path) is None


def test_latest_guard_passing_artifact_wins(tmp_path):
    _write_artifact(tmp_path, "20260601_000000", guard_pass=True)
    _write_artifact(tmp_path, "20260610_000000", guard_pass=True)
    _write_artifact(tmp_path, "20260611_000000", guard_pass=False)  # newest but failed
    artifact = find_latest_artifact(tmp_path)
    assert artifact is not None
    assert artifact.stem == "hkalpha1_20260610_000000"


def test_predict_contract(tmp_path):
    _write_artifact(tmp_path, "20260610_120000", mean_ret=0.004)
    predictor = HKPredictorV2(model_dir=tmp_path)
    model_id = predictor.load_latest()
    assert model_id == "hkalpha1_20260610_120000"

    frame = _live_frame()
    pred = predictor.predict(frame, symbol="0700.HK", context={"mom_2800": 0.002})

    close = float(frame["Close"].iloc[-1])
    assert pred.symbol == "0700.HK"
    assert pred.expected_return == pytest.approx(0.004)
    assert pred.predicted_price == pytest.approx(close * 1.004)
    assert pred.prob_up == pytest.approx(0.75)
    assert pred.confidence == pytest.approx(0.5)
    assert pred.horizon_bars == 30
    assert pred.model_id == model_id
    assert pred.feature_flags["context_available"] is True


def test_expected_return_is_clipped(tmp_path):
    _write_artifact(tmp_path, "20260610_120000", mean_ret=0.50)
    predictor = HKPredictorV2(model_dir=tmp_path)
    predictor.load_latest()
    pred = predictor.predict(_live_frame(), symbol="0700.HK")
    assert pred.expected_return == pytest.approx(0.04)


def test_predict_before_load_raises(tmp_path):
    predictor = HKPredictorV2(model_dir=tmp_path)
    with pytest.raises(RuntimeError, match="load_latest"):
        predictor.predict(_live_frame(), symbol="0700.HK")
