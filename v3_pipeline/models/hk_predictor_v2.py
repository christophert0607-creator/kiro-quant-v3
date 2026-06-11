"""HKAlpha-1 live predictor wrapper (HK prediction model V2).

Loads the newest guard-passing artifact written by
``self_learn/scripts/train_hk_alpha.py`` and turns a live featured frame into
an ``HKPredictionV2``. The ``predicted_price`` field keeps the existing
``ModelManager.predict()`` price-level contract so the live loop can consume
V2 output without changing its signal logic.

Artifact discovery is sidecar-driven: only ``hkalpha1_*.json`` files with
``guard_pass: true`` are eligible, newest stamp wins. No artifact → the
caller falls back to the champion LSTM.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from v3_pipeline.models.hk_alpha_features import build_hk_alpha_features

ARTIFACT_PREFIX = "hkalpha1"
DEFAULT_MODEL_DIR = Path("self_learn/models")
RETURN_CLIP = 0.04  # matches the trainer's ±4% target clip


@dataclass(frozen=True)
class HKPredictionV2:
    symbol: str
    expected_return: float
    predicted_price: float
    prob_up: float
    confidence: float
    horizon_bars: int
    model_id: str
    feature_flags: dict[str, bool]


def find_latest_artifact(model_dir: Path | str = DEFAULT_MODEL_DIR) -> Path | None:
    """Newest guard-passing artifact pkl, or None."""
    model_dir = Path(model_dir)
    if not model_dir.exists():
        return None
    best: Path | None = None
    for sidecar in sorted(model_dir.glob(f"{ARTIFACT_PREFIX}_*.json"), reverse=True):
        try:
            meta = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not meta.get("guard_pass"):
            continue
        artifact = model_dir / str(meta.get("artifact", ""))
        if artifact.exists():
            best = artifact
            break
    return best


class HKPredictorV2:
    def __init__(self, model_dir: Path | str = DEFAULT_MODEL_DIR) -> None:
        self.model_dir = Path(model_dir)
        self._bundle: dict | None = None
        self._model_id: str = ""

    @property
    def loaded(self) -> bool:
        return self._bundle is not None

    @property
    def model_id(self) -> str:
        return self._model_id

    def load_latest(self) -> str:
        """Load the newest guard-passing artifact. Raises if none exists."""
        artifact = find_latest_artifact(self.model_dir)
        if artifact is None:
            raise FileNotFoundError(
                f"No guard-passing {ARTIFACT_PREFIX} artifact under {self.model_dir}. "
                "Run self_learn/scripts/train_hk_alpha.py first."
            )
        with open(artifact, "rb") as fh:
            bundle = pickle.load(fh)
        for key in ("ret_head", "prob_head", "calibrator", "feature_names", "symbol_te"):
            if key not in bundle:
                raise ValueError(f"Artifact {artifact.name} missing '{key}'")
        self._bundle = bundle
        self._model_id = artifact.stem
        return self._model_id

    def predict(
        self,
        wfa_frame: pd.DataFrame,
        *,
        symbol: str,
        context: Mapping | None = None,
    ) -> HKPredictionV2:
        """Predict from the live featured frame (latest bar = decision bar)."""
        if self._bundle is None:
            raise RuntimeError("HKPredictorV2: call load_latest() before predict()")
        bundle = self._bundle

        ctx = dict(context or {})
        ctx.setdefault("symbol_te", bundle["symbol_te"].get(symbol, 0.0))
        result = build_hk_alpha_features(wfa_frame, context=ctx)
        x = result.frame[bundle["feature_names"]].iloc[[-1]]

        expected_return = float(np.clip(bundle["ret_head"].predict(x)[0], -RETURN_CLIP, RETURN_CLIP))
        raw_prob = float(bundle["prob_head"].predict_proba(x)[0, 1])
        prob_up = float(np.clip(bundle["calibrator"].predict([raw_prob])[0], 0.0, 1.0))
        if not np.isfinite(prob_up):
            prob_up = 0.5
        if not np.isfinite(expected_return):
            expected_return = 0.0

        close = float(wfa_frame["Close"].iloc[-1])
        horizon = int(bundle.get("config", {}).get("max_bars", 30))
        return HKPredictionV2(
            symbol=symbol,
            expected_return=expected_return,
            predicted_price=close * (1.0 + expected_return),
            prob_up=prob_up,
            confidence=abs(2.0 * prob_up - 1.0),
            horizon_bars=horizon,
            model_id=self._model_id,
            feature_flags=dict(result.source_flags),
        )
