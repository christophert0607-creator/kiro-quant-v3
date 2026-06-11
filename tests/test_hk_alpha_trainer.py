import json
import pickle

import numpy as np
import pandas as pd
import pytest

from self_learn.scripts.train_hk_alpha import (
    TrainHKAlphaConfig,
    assemble_dataset,
    count_intraday_sessions,
    load_frames_from_db,
    train_hk_alpha,
)
from v3_pipeline.models.hk_alpha_features import FEATURE_ORDER


def _intraday_frame(seed: int, days: int = 2, bars_per_day: int = 330, vol: float = 0.004) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = []
    for d in range(days):
        day = pd.Timestamp("2026-06-08") + pd.Timedelta(days=d)
        morning = pd.date_range(day + pd.Timedelta(hours=9, minutes=30), periods=150, freq="1min")
        afternoon = pd.date_range(day + pd.Timedelta(hours=13), periods=bars_per_day - 150, freq="1min")
        dates.extend(list(morning) + list(afternoon))
    n = len(dates)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    high = close * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = close * (1 - np.abs(rng.normal(0, vol / 2, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": rng.integers(1_000, 50_000, n).astype(float),
            "RSI_14": 50.0,
            "MACD_HIST": 0.0,
            "ATR_14": close * 0.004,
            "BB_UPPER": close * 1.01,
            "BB_LOWER": close * 0.99,
            "SMA_5": close,
            "SMA_20": close,
        }
    )


def _frames(days: int = 2) -> dict[str, pd.DataFrame]:
    frames = {f"000{i}.HK": _intraday_frame(seed=i, days=days) for i in range(3)}
    frames["2800.HK"] = _intraday_frame(seed=99, days=days)
    return frames


def _loose_cfg(**overrides) -> TrainHKAlphaConfig:
    base = dict(
        min_sessions=2, min_rows=500, n_folds=3, embargo_bars=30,
        min_dir_acc=0.0, min_auc=0.0, max_brier=1.0,
    )
    base.update(overrides)
    return TrainHKAlphaConfig(**base)


def test_assemble_dataset_has_features_labels_and_context():
    data = assemble_dataset(_frames(), _loose_cfg())
    assert not data.empty
    for col in FEATURE_ORDER + ["ret_h", "hit_tp_first", "symbol", "Date"]:
        assert col in data.columns
    # 2800.HK is consumed as context, never trained on.
    assert "2800.HK" not in set(data["symbol"])
    assert (data["flag_context"] == 1.0).all()
    # Context momentum actually merged (non-constant).
    assert data["mom_2800"].abs().max() > 0


def test_count_intraday_sessions_excludes_daily_bars():
    daily = pd.DataFrame(
        {
            "Date": pd.date_range("2026-01-05 16:00", periods=100, freq="B"),
            "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1e6,
        }
    )
    cfg = _loose_cfg(min_rows=1)
    data = assemble_dataset({"0700.HK": daily}, cfg)
    assert count_intraday_sessions(data) == 0


def test_blocked_when_insufficient_sessions():
    result = train_hk_alpha(_frames(days=2), _loose_cfg(min_sessions=60))
    assert result.status == "blocked"
    assert "insufficient_sessions" in result.reason
    assert "required=60" in result.reason
    assert result.artifact_path is None


def test_blocked_by_promotion_guard():
    result = train_hk_alpha(_frames(), _loose_cfg(min_dir_acc=1.01))
    assert result.status == "blocked"
    assert "promotion_guard" in result.reason
    assert result.metrics["guard_pass"] is False
    assert result.artifact_path is None


def test_dry_run_reports_metrics_without_artifact():
    result = train_hk_alpha(_frames(), _loose_cfg(), dry_run=True)
    assert result.status == "ok"
    assert result.reason == "dry_run_no_artifact"
    assert result.artifact_path is None
    assert 0.0 <= result.metrics["prob_auc"] <= 1.0
    assert result.metrics["sessions"] >= 2


def test_full_train_writes_artifact_and_sidecar(tmp_path):
    cfg = _loose_cfg(output_dir=str(tmp_path))
    result = train_hk_alpha(_frames(), cfg)
    assert result.status == "ok"
    assert result.artifact_path and result.sidecar_path

    with open(result.artifact_path, "rb") as fh:
        bundle = pickle.load(fh)
    assert bundle["feature_names"] == list(FEATURE_ORDER)
    assert set(bundle["symbol_te"]) == {"0000.HK", "0001.HK", "0002.HK"}

    # Round-trip inference on the saved heads.
    data = assemble_dataset(_frames(), cfg)
    X = data[FEATURE_ORDER].head(5)
    ret_pred = bundle["ret_head"].predict(X)
    prob_raw = bundle["prob_head"].predict_proba(X)[:, 1]
    prob_cal = np.clip(bundle["calibrator"].predict(prob_raw), 0.0, 1.0)
    assert np.isfinite(ret_pred).all()
    assert ((prob_cal >= 0.0) & (prob_cal <= 1.0)).all()

    sidecar = json.loads(open(result.sidecar_path).read())
    assert sidecar["guard_pass"] is True
    assert sidecar["metrics"]["rows"] > 0


def test_load_frames_from_missing_db_returns_empty(tmp_path):
    assert load_frames_from_db(tmp_path / "nope.db") == {}
