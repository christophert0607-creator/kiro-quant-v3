#!/usr/bin/env python3
"""Meta-labeling — export model as joblib for live integration.

Milestone: M6.live_integration

This script converts the trained model (stored as JSON by baseline_model.py)
into a joblib file that meta_gate.py can load.

Output: dev/meta_labeling/out/meta_model.joblib

Usage:
    python3 dev/meta_labeling/export_joblib.py
"""

import json
import sys
import numpy as np
from pathlib import Path

try:
    import joblib
except ImportError:
    print("ERROR: joblib not installed. Install with: pip install joblib")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent
OUT_DIR = SCRIPT_DIR / "out"
MODEL_JSON_PATH = OUT_DIR / "model_weights.json"
JOBLIB_PATH = OUT_DIR / "meta_model.joblib"


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
    
    # Store model params directly in dict (no custom classes needed)
    model_params = {
        "type": "logistic_regression",
        "coef": coef_list,
        "intercept": intercept,
        "feature_names": feature_names,
    }
    
    # Create scaler params
    scaler = {
        "mean": scaler_mean,
        "scale": scaler_std,
        "n_features_in_": len(feature_names)
    }
    
    # Bundle everything - meta_gate.py will handle dict models
    model_bundle = {
        "model": model_params,
        "scaler": scaler,
        "feature_names": feature_names,
        "metrics": data.get("metrics", {}),
    }
    
    # Save as joblib
    joblib.dump(model_bundle, JOBLIB_PATH)
    
    print(f"✓ Exported model to {JOBLIB_PATH}")
    print(f"  Features: {len(feature_names)}")
    print(f"  Samples: {data.get('metrics', {}).get('n_samples', 'N/A')}")
    print(f"  AUC: {data.get('metrics', {}).get('auc', 'N/A')}")


if __name__ == "__main__":
    main()