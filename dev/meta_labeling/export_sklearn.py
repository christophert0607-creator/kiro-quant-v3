#!/usr/bin/env python3
"""Meta-labeling — export model as sklearn-compatible joblib for live integration.

Milestone: M6.live_integration

This script converts the trained model (stored as JSON by baseline_model.py)
into a sklearn-compatible .joblib file that meta_gate.py can load and use.

Output: dev/meta_labeling/out/meta_model.joblib

The exported model provides predict_proba() for probability scoring.

Usage:
    python3 dev/meta_labeling/export_sklearn.py
"""

import json
import sys
from pathlib import Path

try:
    import joblib
    import numpy as np
    from sklearn.base import BaseEstimator, ClassifierMixin
    from sklearn.preprocessing import StandardScaler
except ImportError:
    print("ERROR: Required packages not installed.")
    print("Install with: pip install scikit-learn joblib")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent
OUT_DIR = SCRIPT_DIR / "out"
MODEL_JSON_PATH = OUT_DIR / "model_weights.json"
JOBLIB_PATH = OUT_DIR / "meta_model.joblib"


class PretrainedLogisticRegression(BaseEstimator, ClassifierMixin):
    """LogisticRegression with pre-set coefficients (no fitting required)."""
    
    def __init__(self, coef, intercept, feature_names):
        self.coef_ = np.array(coef)
        self.intercept_ = float(intercept)
        self.feature_names_in_ = feature_names
        self.classes_ = np.array([0, 1])
    
    def predict_proba(self, X):
        """Return probability predictions."""
        # Ensure X is 2D
        if X.ndim == 1:
            X = X.reshape(1, -1)
        
        # Compute logit
        logit = np.dot(X, self.coef_) + self.intercept_
        
        # Sigmoid
        prob = 1.0 / (1.0 + np.exp(-logit))
        
        # Return [P(0), P(1)] format
        return np.column_stack([1 - prob, prob])
    
    def predict(self, X):
        """Return class predictions."""
        proba = self.predict_proba(X)
        return self.classes_[np.argmax(proba, axis=1)]


def main():
    if not MODEL_JSON_PATH.exists():
        print(f"ERROR: Model JSON not found at {MODEL_JSON_PATH}")
        sys.exit(1)
    
    with open(MODEL_JSON_PATH) as f:
        data = json.load(f)
    
    weights = data.get("weights", {})
    feature_names = data.get("feature_names", [])
    scaler_mean = data.get("scaler_mean", [])
    scaler_std = data.get("scaler_std", [])
    
    # Extract coefficients
    coef_list = [weights.get(fn, 0.0) for fn in feature_names]
    intercept = weights.get("intercept", 0.0)
    
    # Create model with pre-trained parameters
    model = PretrainedLogisticRegression(
        coef=coef_list,
        intercept=intercept,
        feature_names=feature_names
    )
    
    # Create scaler with pre-trained parameters
    scaler = StandardScaler()
    scaler.mean_ = np.array(scaler_mean)
    scaler.scale_ = np.array(scaler_std)
    scaler.n_features_in_ = len(feature_names)
    scaler.n_samples_seen_ = data.get("metrics", {}).get("n_samples", 34)
    
    # Bundle model + scaler as expected by meta_gate.py
    model_bundle = {
        "model": model,
        "scaler": scaler,
        "feature_names": feature_names,
        "metrics": data.get("metrics", {}),
    }
    
    # Save as joblib
    joblib.dump(model_bundle, JOBLIB_PATH)
    
    print(f"✓ Exported sklearn model to {JOBLIB_PATH}")
    print(f"  Features: {len(feature_names)}")
    print(f"  Samples: {data.get('metrics', {}).get('n_samples', 'N/A')}")
    print(f"  AUC: {data.get('metrics', {}).get('auc', 'N/A')}")
    
    # Verify we can load it
    loaded = joblib.load(JOBLIB_PATH)
    m = loaded["model"]
    print(f"  Model type: {type(m).__name__}")
    print(f"  Has predict_proba: {hasattr(m, 'predict_proba')}")


if __name__ == "__main__":
    main()