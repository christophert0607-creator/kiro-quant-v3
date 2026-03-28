"""Meta-labeling live gate (US SIM).

This is the *live integration* thin wrapper. It loads a pre-trained meta model
artifact (produced by offline dev/meta_labeling work) and returns a probability
score used to hard-gate BUY decisions.

Safety:
- Best-effort: if model cannot be loaded or inference fails, it returns None.
- Activation is controlled by env:
  - ENABLE_META_LABELING=1
  - META_LABELING_MODE=us_sim
  - META_THRESHOLD=0.50 (used by caller)

Model discovery:
- If META_MODEL_PATH is set, use it.
- Else pick the newest *.joblib/*.pkl under dev/meta_labeling/.

Expected model interface:
- sklearn-like: predict_proba(X) -> [p0,p1]
- or predict(X) -> {0,1}

Feature alignment:
- If model has feature_names_in_, we build a 1-row DataFrame with those columns.
- Otherwise we build DataFrame from provided feature dict keys.
"""

from __future__ import annotations

import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MetaModel:
    model: Any
    feature_names: list[str] | None = None
    scaler: Any = None


_CACHE: MetaModel | None = None


def _workspace_root() -> Path:
    # kiro-quant-v3/v3_pipeline/ml -> kiro-quant-v3 -> workspace-quant
    return Path(__file__).resolve().parents[3]


def _discover_model_path() -> Path | None:
    env_path = os.getenv("META_MODEL_PATH", "").strip()
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    base = _workspace_root() / "dev" / "meta_labeling"
    if not base.exists():
        return None

    candidates = list(base.rglob("*.joblib")) + list(base.rglob("*.pkl"))
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        return None

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _load_artifact(path: Path) -> Any:
    # Try joblib if available and file looks like joblib
    if path.suffix.lower() == ".joblib":
        try:
            import joblib  # type: ignore

            return joblib.load(path)
        except Exception:
            pass

    # Fallback to pickle
    with open(path, "rb") as f:
        return pickle.load(f)


def load_meta_model() -> MetaModel | None:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    path = _discover_model_path()
    if path is None:
        return None

    obj = _load_artifact(path)

    # Common patterns: dict bundle
    model = obj
    feature_names = None
    scaler = None
    if isinstance(obj, dict):
        model = obj.get("model") or obj.get("clf") or obj.get("estimator") or obj
        feature_names = obj.get("feature_names") or obj.get("features")
        scaler = obj.get("scaler")

    # sklearn models: feature_names_in_
    try:
        if feature_names is None and hasattr(model, "feature_names_in_"):
            feature_names = [str(x) for x in list(getattr(model, "feature_names_in_"))]
    except Exception:
        feature_names = feature_names

    _CACHE = MetaModel(model=model, feature_names=feature_names)
    _CACHE.scaler = scaler  # type: ignore
    return _CACHE


def score(features: dict[str, Any]) -> float | None:
    mm = load_meta_model()
    if mm is None:
        return None

    try:
        import pandas as pd  # type: ignore
        import numpy as np

        cols = mm.feature_names or sorted(features.keys())
        row = {c: features.get(c, 0) for c in cols}
        X = pd.DataFrame([row], columns=cols)

        m = mm.model
        
        # Handle dict model with coef/intercept (our JSON-exported format)
        if isinstance(m, dict) and m.get("type") == "logistic_regression":
            coef = np.array(m.get("coef", []))
            intercept = float(m.get("intercept", 0.0))
            
            # Apply scaler if available
            X_scaled = X.values
            if mm.scaler:
                mean = np.array(mm.scaler.get("mean", [0] * len(cols)))
                scale = np.array(mm.scaler.get("scale", [1] * len(cols)))
                X_scaled = (X.values - mean) / scale
            
            logit = np.dot(X_scaled, coef) + intercept
            prob = 1.0 / (1.0 + np.exp(-logit))
            return float(prob[0])
        
        # Handle sklearn models with predict_proba
        if hasattr(m, "predict_proba"):
            proba = m.predict_proba(X)
            return float(proba[0][1])
        
        # Handle sklearn models with just predict
        if hasattr(m, "predict"):
            pred = m.predict(X)
            return float(pred[0])
        
        return None
    except Exception as e:
        # Best-effort: return None on any error
        import logging
        logging.getLogger(__name__).warning(f"meta_gate score error: {e}")
        return None


def enabled() -> bool:
    if os.getenv("ENABLE_META_LABELING", "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return os.getenv("META_LABELING_MODE", "").strip().lower() == "us_sim"


def no_score_policy() -> str:
    return os.getenv("META_NO_SCORE_POLICY", "allow_small").strip().lower() or "allow_small"


def no_score_size_mult() -> float:
    try:
        return float(os.getenv("META_NO_SCORE_SIZE_MULT", "0.25"))
    except Exception:
        return 0.25
