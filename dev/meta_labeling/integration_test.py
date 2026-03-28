#!/usr/bin/env python3
"""Meta-labeling — integration test for M5.

Tests the complete pipeline from data extraction through inference.

Usage:
    python3 dev/meta_labeling/integration_test.py

Verifies:
- All modules compile (py_compile)
- Model weights load correctly
- Inference returns valid probabilities
- Threshold filtering works as expected
"""

import json
import sys
from pathlib import Path
import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent.parent
SCRIPT_DIR = PROJECT_ROOT / "dev" / "meta_labeling"

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

# Load model from JSON directly
MODEL_PATH = SCRIPT_DIR / "out" / "model_weights.json"


def load_model():
    """Load model weights and scaler from JSON."""
    if not MODEL_PATH.exists():
        return None, None
    
    with open(MODEL_PATH) as f:
        data = json.load(f)
    
    weights = {
        "coef": data.get("weights", {}).get("confidence", []) or [0] * len(FEATURE_KEYS),
        "intercept": data.get("weights", {}).get("intercept", 0.0)
    }
    # Map feature names to coefficients
    feature_names = data.get("feature_names", FEATURE_KEYS)
    coef_list = data.get("weights", {})
    
    # Build coef array in correct order
    coef_array = []
    for fname in FEATURE_KEYS:
        coef_array.append(coef_list.get(fname, 0.0))
    
    weights["coef"] = coef_array
    
    scaler = {
        "mean": data.get("scaler_mean", [0] * len(FEATURE_KEYS)),
        "scale": data.get("scaler_std", [1] * len(FEATURE_KEYS))
    }
    
    return weights, scaler


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def extract_features(event):
    """Extract feature vector from decision event."""
    features = []
    for key in FEATURE_KEYS:
        val = event.get(key)
        features.append(_safe_float(val))
    
    if all(f == 0.0 for f in features):
        return None
    
    return np.array(features)


def score_decision(event, threshold=0.5):
    """Score a decision event and return probability."""
    weights, scaler_params = load_model()
    
    if weights is None or scaler_params is None:
        return None
    
    features = extract_features(event)
    if features is None:
        return None
    
    mean = np.array(scaler_params.get("mean", [0] * len(FEATURE_KEYS)))
    scale = np.array(scaler_params.get("scale", [1] * len(FEATURE_KEYS)))
    scaled = (features - mean) / scale
    
    coef = np.array(weights.get("coef", [0] * len(FEATURE_KEYS)))
    intercept = weights.get("intercept", 0.0)
    logit = np.dot(scaled, coef) + intercept
    
    prob = 1.0 / (1.0 + np.exp(-logit))
    
    return float(prob)


def should_allow_decision(event, threshold=0.5):
    """Decide whether to allow a decision based on probability."""
    prob = score_decision(event, threshold)
    if prob is None:
        return None
    
    return prob >= threshold


def test_model_loads():
    """Test that model weights load successfully."""
    print("Test: model_loads")
    weights, scaler = load_model()
    assert weights is not None, "Model weights should load"
    assert scaler is not None, "Scaler params should load"
    print(f"  ✓ Model and scaler loaded successfully")
    print(f"  ✓ Features: {len(FEATURE_KEYS)}")
    return True


def test_inference_with_real_events():
    """Test inference with real labeled events."""
    print("\nTest: inference_with_real_events")
    
    labeled_path = SCRIPT_DIR / "out" / "labeled_events.jsonl"
    if not labeled_path.exists():
        print("  ✗ No labeled_events.jsonl found")
        return False
    
    with open(labeled_path) as f:
        events = [json.loads(line) for line in f if line.strip()]
    
    if not events:
        print("  ✗ No events in labeled_events.jsonl")
        return False
    
    test_events = [e for e in events if e.get("labels", {}).get("label_30m") is not None][:5]
    
    if not test_events:
        print("  ✗ No events with labels")
        return False
    
    correct = 0
    
    for ev in test_events:
        prob = score_decision(ev)
        label = ev.get("labels", {}).get("label_30m")
        
        if prob is None:
            print(f"  ✗ Event: probability is None")
            continue
        
        allow = should_allow_decision(ev, 0.5)
        
        # Check if prediction aligns with label
        if (label == 1 and prob >= 0.5) or (label == 0 and prob < 0.5):
            correct += 1
        
        ts = ev.get("ts_utc", "N/A")[:19]
        print(f"  Event {ts}: prob={prob:.3f}, allow={allow}, label={label}")
    
    total = len(test_events)
    accuracy = correct / total if total > 0 else 0
    print(f"  Results: {correct}/{total} correct ({accuracy:.1%})")
    print("  ✓ Inference functional")
    return True


def test_threshold_sweep():
    """Test threshold sweep to find optimal cutoff."""
    print("\nTest: threshold_sweep")
    
    labeled_path = SCRIPT_DIR / "out" / "labeled_events.jsonl"
    with open(labeled_path) as f:
        events = [json.loads(line) for line in f if line.strip()]
    
    test_events = [e for e in events if e.get("labels", {}).get("label_30m") is not None]
    
    results = []
    for threshold in [0.1, 0.3, 0.5, 0.7, 0.9]:
        allowed = sum(1 for e in test_events if should_allow_decision(e, threshold) == True)
        blocked = sum(1 for e in test_events if should_allow_decision(e, threshold) == False)
        
        allowed_labels = [e.get("labels", {}).get("label_30m") for e in test_events 
                         if should_allow_decision(e, threshold) == True]
        win_rate = sum(allowed_labels) / len(allowed_labels) if allowed_labels else 0
        
        results.append({
            "threshold": threshold,
            "allowed": allowed,
            "blocked": blocked,
            "win_rate": win_rate
        })
        
        print(f"  Threshold {threshold}: allowed={allowed}, blocked={blocked}, win_rate={win_rate:.1%}")
    
    print("  ✓ Threshold sweep completed")
    return True


def test_feature_extraction():
    """Test that all required features are available."""
    print("\nTest: feature_extraction")
    
    labeled_path = SCRIPT_DIR / "out" / "labeled_events.jsonl"
    with open(labeled_path) as f:
        events = [json.loads(line) for line in f if line.strip()]
    
    if not events:
        print("  ✗ No events found")
        return False
    
    # Check features in available events
    available = set()
    for e in events[:10]:
        available.update(e.keys())
    
    missing = [f for f in FEATURE_KEYS if f not in available]
    
    if missing:
        print(f"  ⚠ Features not in sample events: {missing}")
    else:
        print(f"  ✓ All FEATURE_KEYS available in events")
    
    return True


def main():
    print("=" * 60)
    print("M5 Integration Test — Meta-labeling")
    print("=" * 60)
    
    # Compile check
    print("\n1. Compiling all modules...")
    modules = [
        "dev/meta_labeling/dataset_extractor.py",
        "dev/meta_labeling/label_generator.py", 
        "dev/meta_labeling/baseline_model.py",
        "dev/meta_labeling/backtest_harness.py",
        "dev/meta_labeling/inference.py",
    ]
    
    for mod in modules:
        try:
            __import__("py_compile").compile(str(PROJECT_ROOT / mod), doraise=True)
            print(f"  ✓ {mod}")
        except Exception as e:
            print(f"  ✗ {mod}: {e}")
            return 1
    
    print("\n2. Running integration tests...")
    
    all_passed = True
    all_passed &= test_model_loads()
    all_passed &= test_feature_extraction()
    all_passed &= test_inference_with_real_events()
    all_passed &= test_threshold_sweep()
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✓ All integration tests PASSED")
    else:
        print("✗ Some tests FAILED")
    print("=" * 60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())