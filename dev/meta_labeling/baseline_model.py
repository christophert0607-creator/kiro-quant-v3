#!/usr/bin/env python3
"""Meta-labeling - baseline model (offline).

Milestone: M2.baseline_model

Reads:
- dev/meta_labeling/out/labeled_events.jsonl (from M1)

Outputs:
- dev/meta_labeling/out/model_metrics.json
- dev/meta_labeling/out/model_weights.json (feature coefficients)

Features (available from labeling):
- confidence: decision confidence score
- snapshot_total_assets: account equity
- snapshot_cash: available cash
- snapshot_market_val: position market value
- ohlcv_* fields (if available)
- ind_* technical indicators (if available)

Model: LogisticRegression with StandardScaler

Metrics: accuracy, precision, recall, AUC, calibration
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, average_precision_score,
                             brier_score_loss, precision_recall_fscore_support, roc_auc_score)
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler


FEATURE_KEYS = [
    "confidence",
    "snapshot_total_assets",
    "snapshot_cash",
    "snapshot_market_val",
    # OHLCV features (if available)
    "ohlcv_volume",
    # Indicators (if available)
    "ind_sma_5",
    "ind_sma_20",
    "ind_rsi_14",
    "ind_macd",
    "ind_bb_upper",
    "ind_bb_lower",
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_labeled_events(path: str) -> list[dict[str, Any]]:
    """Load labeled events from JSONL."""
    events = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def build_feature_matrix(events: list[dict[str, Any]]) -> tuple[np.ndarray, list[str]]:
    """Build feature matrix from events, handling missing values."""
    feature_names = [k for k in FEATURE_KEYS if k in events[0] or any(k in e for e in events)]
    if not feature_names:
        # Fallback: use numeric keys from first event
        sample = events[0]
        feature_names = [k for k in sample.keys()
                        if isinstance(sample[k], (int, float)) and k != "label"]

    X = np.zeros((len(events), len(feature_names)))
    for i, ev in enumerate(events):
        for j, feat in enumerate(feature_names):
            X[i, j] = _safe_float(ev.get(feat), 0.0)

    return X, feature_names


def build_label_vector(events: list[dict[str, Any]], horizon: str = "30m") -> np.ndarray:
    """Build binary label vector."""
    labels = []
    for ev in events:
        lbl = ev.get("labels", {}).get(f"label_{horizon}")
        if lbl is not None:
            labels.append(lbl)
        else:
            # Fallback to composite label
            labels.append(ev.get("label", 0))
    return np.array(labels)


def train_baseline(X: np.ndarray, y: np.ndarray, feature_names: list[str]) -> dict[str, Any]:
    """Train LogisticRegression baseline and compute metrics."""
    if len(X) < 2:
        return {"error": "Insufficient samples for training", "n_samples": len(X)}

    if y.sum() == 0:
        return {"error": "No positive labels", "n_positive": 0}

    if y.sum() == len(y):
        return {"error": "No negative labels", "n_negative": 0}

    # Scale features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Train model
    model = LogisticRegression(max_iter=200, random_state=42, class_weight="balanced")
    model.fit(X_scaled, y)

    # Predictions
    y_pred = model.predict(X_scaled)
    y_proba = model.predict_proba(X_scaled)[:, 1]

    # Metrics
    metrics = {
        "n_samples": len(X),
        "n_features": X.shape[1],
        "n_positive": int(y.sum()),
        "n_negative": int(len(y) - y.sum()),
        "accuracy": float(accuracy_score(y, y_pred)),
    }

    # Precision, recall
    prec, rec, f1, _ = precision_recall_fscore_support(y, y_pred, average="binary", zero_division=0)
    metrics["precision"] = float(prec)
    metrics["recall"] = float(rec)
    metrics["f1"] = float(f1)
    metrics["brier_score"] = float(brier_score_loss(y, y_proba))

    # AUC (only if both classes present)
    if len(np.unique(y)) == 2:
        metrics["auc"] = float(roc_auc_score(y, y_proba))
        metrics["average_precision"] = float(average_precision_score(y, y_proba))

    # Cross-validation (if enough samples)
    if len(X) >= 5:
        try:
            cv_scores = cross_val_score(model, X_scaled, y, cv=min(3, len(X)), scoring="accuracy")
            metrics["cv_accuracy_mean"] = float(cv_scores.mean())
            metrics["cv_accuracy_std"] = float(cv_scores.std())
        except Exception as e:
            metrics["cv_error"] = str(e)

    # Feature importance (coefficients)
    weights = {feat: float(coef) for feat, coef in zip(feature_names, model.coef_[0])}
    weights["intercept"] = float(model.intercept_[0])

    return {
        "metrics": metrics,
        "weights": weights,
        "feature_names": feature_names,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_std": scaler.scale_.tolist(),
    }


def main():
    parser = argparse.ArgumentParser(description="Train baseline meta-labeling model")
    parser.add_argument("--input", "-i", default="dev/meta_labeling/out/labeled_events.jsonl",
                        help="Input labeled events JSONL")
    parser.add_argument("--out-metrics", "-o", default="dev/meta_labeling/out/model_metrics.json",
                        help="Output metrics JSON")
    parser.add_argument("--out-weights", default="dev/meta_labeling/out/model_weights.json",
                        help="Output weights JSON")
    parser.add_argument("--horizon", "-H", default="30m",
                        help="Label horizon (e.g., 30m, 60m)")
    parser.add_argument("--min-samples", "-m", type=int, default=3,
                        help="Minimum samples required")

    args = parser.parse_args()

    # Load events
    print(f"Loading events from {args.input}...")
    events = load_labeled_events(args.input)
    print(f"Loaded {len(events)} events")

    # Filter to valid events (has ohlcv and labels)
    valid_events = [
        e for e in events
        if not e.get("ohlcv_error") and e.get("labels", {}).get(f"label_{args.horizon}") is not None
    ]
    print(f"Valid events (OHLCV + label): {len(valid_events)}")

    if len(valid_events) < args.min_samples:
        print(f"⚠️  Not enough valid samples ({len(valid_events)} < {args.min_samples})")
        print("Building feature matrix anyway with all events...")
        # Use all events with any label
        valid_events = [e for e in events if e.get("labels", {}).get(f"label_{args.horizon}") is not None]
        if not valid_events:
            valid_events = [e for e in events if e.get("label") is not None]
        print(f"Using {len(valid_events)} events with labels")

    # Build feature matrix
    X, feature_names = build_feature_matrix(valid_events)
    y = build_label_vector(valid_events, args.horizon)

    print(f"Feature matrix: {X.shape}")
    print(f"Features: {feature_names}")
    print(f"Labels: {y.sum()} positive, {len(y) - y.sum()} negative")

    # Train model
    result = train_baseline(X, y, feature_names)

    if "error" in result:
        print(f"⚠️  {result['error']}")
        # Save partial result
        with open(args.out_metrics, "w") as f:
            json.dump(result, f, indent=2)
        return

    # Print metrics
    print("\n=== Model Metrics ===")
    m = result["metrics"]
    print(f"Accuracy: {m.get('accuracy', 'N/A'):.3f}")
    print(f"Precision: {m.get('precision', 'N/A'):.3f}")
    print(f"Recall: {m.get('recall', 'N/A'):.3f}")
    print(f"F1: {m.get('f1', 'N/A'):.3f}")
    print(f"AUC: {m.get('auc', 'N/A'):.3f}")
    print(f"Average Precision: {m.get('average_precision', 'N/A'):.3f}")
    if "cv_accuracy_mean" in m:
        print(f"CV Accuracy: {m['cv_accuracy_mean']:.3f} ± {m['cv_accuracy_std']:.3f}")

    # Print top features
    print("\n=== Feature Weights (top 5) ===")
    weights = result["weights"]
    sorted_weights = sorted(weights.items(), key=lambda x: abs(x[1]), reverse=True)
    for feat, w in sorted_weights[:5]:
        print(f"  {feat}: {w:+.4f}")

    # Save outputs
    with open(args.out_metrics, "w") as f:
        json.dump(result["metrics"], f, indent=2)
    print(f"\nMetrics saved to {args.out_metrics}")

    with open(args.out_weights, "w") as f:
        json.dump(result, f, indent=2, default=list)
    print(f"Weights saved to {args.out_weights}")

    # Also export dedicated scaler_params.json for inference module
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from inference import export_scaler_params
        weights_path = Path(args.out_weights)
        if export_scaler_params(weights_path):
            print(f"Scaler params exported to scaler_params.json")
        else:
            print("Scaler params export failed (non-fatal)")
    except Exception as e:
        print(f"Scaler params export skipped: {e}")


if __name__ == "__main__":
    main()
