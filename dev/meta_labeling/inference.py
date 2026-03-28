#!/usr/bin/env python3
"""Meta-labeling — inference module for live integration.

Milestone: M4.integration

This module provides a scoring function that can be called from trading logic
to filter BUY decisions using the trained meta-model.

Usage:
    from dev.meta_labeling.inference import score_decision
    score = score_decision(decision_event)

The model and scaler are loaded from:
- dev/meta_labeling/out/model_weights.json (LogisticRegression coefficients + intercept)
- dev/meta_labeling/out/scaler_params.json (StandardScaler mean/std)

If model files don't exist, returns None (passthrough, no filtering).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

# Paths
SCRIPT_DIR = Path(__file__).parent
OUT_DIR = SCRIPT_DIR / "out"
MODEL_WEIGHTS_PATH = OUT_DIR / "model_weights.json"
SCALER_PARAMS_PATH = OUT_DIR / "scaler_params.json"

# Feature keys (must match training)
FEATURE_KEYS = [
    "confidence",
    "snapshot_total_assets",
    "snapshot_cash",
    "snapshot_market_val",
    "ohlcv_volume",
    "ind_sma_5",
    "ind_sma_20",
    "ind_rsi_14",
    "ind_macd",
    "ind_bb_upper",
    "ind_bb_lower",
]

# Loaded model state
_model_weights: dict | None = None
_scaler_params: dict | None = None
_feature_names: list[str] | None = None


def _load_model() -> tuple[dict | None, dict | None, list[str] | None]:
    """Load model weights and scaler parameters.
    
    Prefers scaler_params.json for scaler, falls back to scaler embedded
    in model_weights.json for backward compatibility.
    """
    global _model_weights, _scaler_params, _feature_names

    if _model_weights is not None and _scaler_params is not None and _feature_names is not None:
        return _model_weights, _scaler_params, _feature_names

    if MODEL_WEIGHTS_PATH.exists():
        with open(MODEL_WEIGHTS_PATH) as f:
            data = json.load(f)
            _model_weights = data.get("weights", {})
            raw_features = data.get("feature_names")
            _feature_names = list(raw_features) if raw_features else list(FEATURE_KEYS)

    # Prefer dedicated scaler_params.json, fall back to embedded scaler in weights
    if SCALER_PARAMS_PATH.exists():
        with open(SCALER_PARAMS_PATH) as f:
            sp = json.load(f)
            _scaler_params = {
                "mean": sp.get("mean", []),
                "scale": sp.get("std", []),
            }
    elif MODEL_WEIGHTS_PATH.exists():
        with open(MODEL_WEIGHTS_PATH) as f:
            data = json.load(f)
            _scaler_params = {
                "mean": data.get("scaler_mean", []),
                "scale": data.get("scaler_std", []),
            }
    else:
        _model_weights = None
        _scaler_params = None
        _feature_names = list(FEATURE_KEYS)

    if _model_weights is None:
        _model_weights = {}
        _feature_names = list(FEATURE_KEYS)

    return _model_weights, _scaler_params, _feature_names


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert value to float safely."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_features(event: dict[str, Any], feature_names: list[str]) -> np.ndarray | None:
    """Extract feature vector from decision event."""
    features = []
    for key in feature_names:
        val = event.get(key)
        features.append(_safe_float(val))

    # Check if we have meaningful features (not all zeros)
    if all(f == 0.0 for f in features):
        return None

    return np.array(features)


def score_decision(event: dict[str, Any], threshold: float = 0.5) -> float | None:
    """Score a decision event and return probability of being a winner.

    Args:
        event: Decision event dict with features (confidence, snapshot_*, ohlcv_*, ind_*)
        threshold: Probability threshold for classification (not used in return value)

    Returns:
        Probability (0-1) if model loaded successfully, None if no model available
    """
    weights, scaler_params, feature_names = _load_model()

    if weights is None or scaler_params is None:
        return None

    feature_names = feature_names or list(FEATURE_KEYS)
    features = _extract_features(event, feature_names)
    if features is None:
        return None

    mean = np.array(scaler_params.get("mean", []), dtype=float)
    scale = np.array(scaler_params.get("scale", []), dtype=float)

    if mean.shape[0] != len(feature_names):
        mean = np.zeros(len(feature_names), dtype=float)
    if scale.shape[0] != len(feature_names) or np.any(scale == 0):
        scale = np.where(scale == 0, 1, scale)
        if scale.shape[0] != len(feature_names):
            scale = np.ones(len(feature_names), dtype=float)

    scaled = (features - mean) / scale

    coef = np.array([_safe_float(weights.get(feat, 0.0)) for feat in feature_names])
    intercept = _safe_float(weights.get("intercept", 0.0))
    logit = np.dot(scaled, coef) + intercept

    prob = 1.0 / (1.0 + np.exp(-logit))

    return float(prob)


def should_allow_decision(event: dict[str, Any], threshold: float = 0.5) -> bool | None:
    """Decide whether to allow a decision based on meta-model probability.

    Args:
        event: Decision event dict
        threshold: Minimum probability to allow (default 0.5)

    Returns:
        True if should allow, False if should block, None if no model available
    """
    prob = score_decision(event, threshold)
    if prob is None:
        return None

    return prob >= threshold


def export_scaler_params(weights_path: Path = MODEL_WEIGHTS_PATH) -> bool:
    """Export scaler parameters to scaler_params.json for inference module.

    Reads scaler_mean/scaler_std from model_weights.json and writes a
    dedicated scaler_params.json so live gating and training share the same
    scaling metadata.

    Args:
        weights_path: Path to model_weights.json (for testing)

    Returns:
        True if exported successfully
    """
    try:
        if not weights_path.exists():
            return False
        with open(weights_path) as f:
            data = json.load(f)

        scaler = {
            "schema": "kiro.meta_labeling.scaler_params.v1",
            "feature_names": data.get("feature_names", FEATURE_KEYS),
            "mean": data.get("scaler_mean", []),
            "std": data.get("scaler_std", []),
        }

        with open(SCALER_PARAMS_PATH, "w") as f:
            json.dump(scaler, f, indent=2)

        return True
    except Exception:
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Meta-labeling inference")
    parser.add_argument("--event", type=str, help="JSON event to score")
    parser.add_argument("--threshold", type=float, default=0.5, help="Threshold")
    parser.add_argument("--list-features", action="store_true", help="Show feature keys")
    parser.add_argument("--export-scalers", action="store_true", help="Export scaler params to scaler_params.json")
    args = parser.parse_args()

    if args.export_scalers:
        ok = export_scaler_params()
        if ok:
            print(f"Exported scaler params to {SCALER_PARAMS_PATH}")
        else:
            print("Export failed")
    elif args.list_features:
        _, _, feature_names = _load_model()
        print("Features:", feature_names or FEATURE_KEYS)
    elif args.event:
        event = json.loads(args.event)
        prob = score_decision(event, args.threshold)
        if prob is None:
            print("No model loaded or invalid event")
        else:
            allow = should_allow_decision(event, args.threshold)
            print(f"Probability: {prob:.3f}, Allow: {allow}")
    else:
        print("Usage: inference.py --event '{...}' or --list-features or --export-scalers")
