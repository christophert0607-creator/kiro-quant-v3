"""HK prediction model V2 (HKAlpha-1) shadow integration tests.

Covers the live-loop helpers (_get_hk_predictor_v2 / _maybe_hk_model_v2),
config plumbing through v3_launcher, and the fallback path when no
guard-passing artifact exists.
"""

import json
import pickle
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.isotonic import IsotonicRegression

# Stub the torch-backed manager module before main_loop/v3_launcher import it
# (same convention as test_live_execution_ordering.py).
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
sys.modules.setdefault("v3_pipeline.models.brain", SimpleNamespace(KiroLSTM=object))

import v3_launcher
import self_learn.models as sl_models
from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop
from v3_pipeline.models.hk_alpha_features import FEATURE_ORDER
from v3_pipeline.models.hk_predictor_v2 import HKPredictorV2


# ── fakes (same shape as test_live_execution_ordering.py) ────────────────────

class _FakeConnector:
    def get_account_info(self):
        return {"total_assets": 100_000.0, "cash": 100_000.0}

    def get_positions(self):
        return pd.DataFrame([])

    def validate_trading_ready(self):
        pass

    def _resolved_trd_env(self):
        return "SIMULATE"


class _FakeModelManager:
    data_preparer = SimpleNamespace(
        lookback=2, target_col="Close", is_fitted=True, feature_columns=None,
        fit_transform=lambda frame: frame,
    )

    def predict(self, *a, **kw):
        return 0.0


def _make_loop(**cfg_overrides) -> LiveTradingLoop:
    cfg = LiveConfig(
        symbol="0700.HK",
        symbols_list=["0700.HK"],
        auto_trade=False,
        paper_trading=True,
        polling_seconds=1,
        **cfg_overrides,
    )
    return LiveTradingLoop(
        model_manager=_FakeModelManager(),
        risk_controller=SimpleNamespace(),
        futu_connector=_FakeConnector(),
        data_manager=None,
        feature_generator=SimpleNamespace(generate=lambda f: f),
        config=cfg,
    )


def _write_artifact(model_dir, stamp: str = "20260610_120000", mean_ret: float = 0.004):
    n_feat = len(FEATURE_ORDER)
    X = np.zeros((4, n_feat))
    bundle = {
        "ret_head": DummyRegressor(strategy="constant", constant=mean_ret).fit(X, [mean_ret] * 4),
        "prob_head": DummyClassifier(strategy="prior").fit(X, [0, 1, 1, 1]),
        "calibrator": IsotonicRegression(out_of_bounds="clip").fit([0.0, 0.5, 1.0], [0.0, 0.5, 1.0]),
        "feature_names": list(FEATURE_ORDER),
        "symbol_te": {"0700.HK": 0.001},
        "config": {"max_bars": 30},
    }
    artifact = model_dir / f"hkalpha1_{stamp}.pkl"
    with open(artifact, "wb") as fh:
        pickle.dump(bundle, fh)
    (model_dir / f"hkalpha1_{stamp}.json").write_text(
        json.dumps({"artifact": artifact.name, "guard_pass": True})
    )


def _live_frame(n: int = 70, price: float = 300.0) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    close = price * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    return pd.DataFrame(
        {
            "Date": pd.date_range("2026-06-10 09:30", periods=n, freq="1min"),
            "Open": close, "High": close * 1.001, "Low": close * 0.999,
            "Close": close, "Volume": rng.integers(1_000, 9_000, n).astype(float),
            "RSI_14": 55.0, "MACD_HIST": 0.01, "ATR_14": close * 0.005,
            "BB_UPPER": close * 1.01, "BB_LOWER": close * 0.99,
            "SMA_5": close, "SMA_20": close,
        }
    )


def _loaded_predictor(tmp_path) -> HKPredictorV2:
    _write_artifact(tmp_path)
    predictor = HKPredictorV2(model_dir=tmp_path)
    predictor.load_latest()
    return predictor


# ── helper behavior ───────────────────────────────────────────────────────────

def test_disabled_config_returns_none():
    loop = _make_loop(hk_model_v2_enabled=False)
    assert loop._get_hk_predictor_v2() is None


def test_missing_artifact_falls_back_and_caches_failure(monkeypatch, tmp_path):
    import v3_pipeline.models.hk_predictor_v2 as predictor_mod
    monkeypatch.setattr(predictor_mod, "DEFAULT_MODEL_DIR", tmp_path)
    loop = _make_loop(hk_model_v2_enabled=True)
    assert loop._get_hk_predictor_v2() is None
    # Failure cached: no retry on subsequent cycles.
    assert loop._hk_predictor_v2 is False
    assert loop._get_hk_predictor_v2() is None


def test_non_hk_symbol_is_skipped(tmp_path):
    loop = _make_loop(hk_model_v2_enabled=True)
    loop._hk_predictor_v2 = _loaded_predictor(tmp_path)
    assert loop._maybe_hk_model_v2("AAPL", _live_frame(), 100.0, 100.0) is None


def test_shadow_emits_event_and_records_prediction(monkeypatch, tmp_path):
    loop = _make_loop(hk_model_v2_enabled=True, hk_model_v2_mode="shadow")
    loop._hk_predictor_v2 = _loaded_predictor(tmp_path)

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(loop, "_emit_structured", lambda event, **f: events.append((event, f)))
    saved: list[dict] = []
    monkeypatch.setattr(sl_models, "save_prediction", lambda *a, **kw: saved.append(kw) or "pid")

    frame = _live_frame()
    close = float(frame["Close"].iloc[-1])
    v2 = loop._maybe_hk_model_v2("0700.HK", frame, lstm_prediction=close * 1.002, current_price=close)

    assert v2 is not None
    assert v2.predicted_price == pytest.approx(close * 1.004)
    assert [e for e, _ in events] == ["hk_model_v2"]
    payload = events[0][1]
    assert payload["mode"] == "shadow"
    assert payload["model_id"] == "hkalpha1_20260610_120000"
    assert payload["lstm_pred"] == pytest.approx(round(close * 1.002, 6))
    # V2 prediction recorded with its artifact id for the health report split.
    assert saved and saved[0]["model_version_id"] == "hkalpha1_20260610_120000"


def test_inference_failure_returns_none_not_raise(monkeypatch, tmp_path):
    loop = _make_loop(hk_model_v2_enabled=True)
    predictor = _loaded_predictor(tmp_path)
    monkeypatch.setattr(
        predictor, "predict",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    loop._hk_predictor_v2 = predictor
    assert loop._maybe_hk_model_v2("0700.HK", _live_frame(), 100.0, 100.0) is None


# ── config plumbing ───────────────────────────────────────────────────────────

def test_launcher_wires_hk_model_v2_flags(tmp_path):
    cfg = {
        "v3_live": {
            "symbols_list": ["AAPL"],
            "hk_model_v2_enabled": True,
            "hk_model_v2_mode": "Shadow",
        }
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    live = v3_launcher._base_live_config(str(path))
    assert live.hk_model_v2_enabled is True
    assert live.hk_model_v2_mode == "shadow"


def test_hk_live_overlay_overrides_v2_mode(tmp_path, monkeypatch):
    cfg = {
        "v3_live": {"hk_model_v2_enabled": False, "hk_model_v2_mode": "shadow"},
        "hk_live": {"symbols_list": ["0700.HK"], "hk_model_v2_enabled": True, "hk_model_v2_mode": "enforce"},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(v3_launcher, "resolve_market_mode", lambda now=None: "HK")
    live = v3_launcher.build_live_config(str(path))
    assert live.hk_model_v2_enabled is True
    assert live.hk_model_v2_mode == "enforce"


def test_repo_config_has_v2_flags_in_both_sections():
    cfg = json.loads(open("config.json", encoding="utf-8").read())
    for section in ("v3_live", "hk_live"):
        assert cfg[section]["hk_model_v2_enabled"] is True, section
        assert cfg[section]["hk_model_v2_mode"] == "shadow", section
