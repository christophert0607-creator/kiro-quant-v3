#!/usr/bin/env python3
"""Meta-labeling — backtest harness (offline).

Milestone: M3.backtest_harness

Reads:
- dev/meta_labeling/out/labeled_events.jsonl (from M1)
- dev/meta_labeling/out/model_weights.json (trained model)

Simulates:
- Apply meta-model probability threshold to BUY decisions
- Compare "before vs after": trade count, win rate, max DD proxy

Outputs:
- dev/meta_labeling/out/backtest_results.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np


# Same feature keys as baseline_model.py
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


def load_model(path: str) -> dict[str, Any]:
    """Load trained model weights and scaler."""
    with open(path, "r") as f:
        return json.load(f)


def build_feature_matrix(events: list[dict[str, Any]], feature_names: list[str]) -> np.ndarray:
    """Build feature matrix from events, handling missing values."""
    X = np.zeros((len(events), len(feature_names)))
    for i, ev in enumerate(events):
        for j, feat in enumerate(feature_names):
            X[i, j] = _safe_float(ev.get(feat), 0.0)
    return X


def sigmoid(z: np.ndarray) -> np.ndarray:
    """Sigmoid function for logistic regression."""
    return 1 / (1 + np.exp(-np.clip(z, -500, 500)))


def predict_proba(X: np.ndarray, weights: dict[str, Any]) -> np.ndarray:
    """Predict probabilities using loaded model."""
    # Get feature names from weights
    feature_names = [k for k in weights.get("feature_names", FEATURE_KEYS) if k != "intercept"]
    
    # Scale features
    scaler_mean = np.array(weights.get("scaler_mean", [0.0] * len(feature_names)))
    scaler_std = np.array(weights.get("scaler_std", [1.0] * len(feature_names)))
    
    # Ensure X has correct shape
    if X.shape[1] != len(feature_names):
        # Pad or truncate
        if X.shape[1] < len(feature_names):
            X_padded = np.zeros((X.shape[0], len(feature_names)))
            X_padded[:, :X.shape[1]] = X
            X = X_padded
        else:
            X = X[:, :len(feature_names)]
    
    X_scaled = (X - scaler_mean) / scaler_std
    
    # Compute linear combination
    coefs = np.array([weights["weights"].get(f, 0.0) for f in feature_names])
    intercept = weights["weights"].get("intercept", 0.0)
    
    z = X_scaled @ coefs + intercept
    return sigmoid(z)


def run_backtest(events: list[dict[str, Any]], model: dict[str, Any], 
                 threshold: float = 0.5, horizon: str = "30m") -> dict[str, Any]:
    """Run backtest simulation."""
    
    # Filter to BUY_PLACED events (simulating original signal)
    buy_events = [e for e in events if e.get("action") == "BUY_PLACED"]
    
    if not buy_events:
        return {"error": "No BUY_PLACED events found", "n_buy_events": 0}
    
    # Get feature names from model
    feature_names = model.get("feature_names", FEATURE_KEYS)
    
    # Build feature matrix
    X = build_feature_matrix(buy_events, feature_names)
    
    # Predict probabilities
    probs = predict_proba(X, model)
    
    # Get actual labels for evaluation
    labels = np.array([
        e.get("labels", {}).get(f"label_{horizon}", e.get("label", 0))
        for e in buy_events
    ])
    
    # === BASELINE: All signals taken ===
    baseline_trades = len(buy_events)
    baseline_wins = int(labels.sum())
    baseline_win_rate = baseline_wins / baseline_trades if baseline_trades > 0 else 0.0
    
    # === WITH META-FILTER: Only take signals above threshold ===
    filtered_mask = probs >= threshold
    filtered_trades = int(filtered_mask.sum())
    filtered_wins = int(labels[filtered_mask].sum()) if filtered_trades > 0 else 0
    filtered_win_rate = filtered_wins / filtered_trades if filtered_trades > 0 else 0.0
    
    # === Compute outcomes for each trade ===
    # Use forward return as actual P&L proxy
    outcomes = []
    for i, e in enumerate(buy_events):
        fwd_return = e.get("labels", {}).get(f"fwd_return_{horizon}")
        if fwd_return is None:
            fwd_return = 0.0
        outcomes.append({
            "ts": e.get("decision_ts_utc"),
            "symbol": e.get("symbol"),
            "prob": float(probs[i]),
            "label": int(labels[i]),
            "fwd_return": float(fwd_return),
            "taken": bool(filtered_mask[i]) if threshold > 0 else True
        })
    
    # Compute max drawdown proxy (cumulative return)
    cumulative = np.cumsum([o["fwd_return"] for o in outcomes])
    running_max = np.maximum.accumulate(cumulative)
    drawdown = running_max - cumulative
    max_drawdown = float(drawdown.max()) if len(drawdown) > 0 else 0.0
    
    # Baseline DD
    baseline_cum = np.cumsum([e.get("labels", {}).get(f"fwd_return_{horizon}", 0.0) for e in buy_events])
    baseline_max_dd = float((np.maximum.accumulate(baseline_cum) - baseline_cum).max()) if len(baseline_cum) > 0 else 0.0
    
    return {
        "threshold": threshold,
        "horizon": horizon,
        "baseline": {
            "n_trades": baseline_trades,
            "n_wins": baseline_wins,
            "win_rate": baseline_win_rate,
            "max_drawdown": baseline_max_dd,
            "avg_return": float(np.mean([e.get("labels", {}).get(f"fwd_return_{horizon}", 0.0) for e in buy_events])) if buy_events else 0.0,
        },
        "meta_filtered": {
            "n_trades": filtered_trades,
            "n_wins": filtered_wins,
            "win_rate": filtered_win_rate,
            "max_drawdown": max_drawdown,
            "avg_return": float(np.mean([o["fwd_return"] for o in outcomes if o["taken"]])) if filtered_trades > 0 else 0.0,
        },
        "improvement": {
            "win_rate_delta": filtered_win_rate - baseline_win_rate,
            "trade_reduction_pct": (baseline_trades - filtered_trades) / baseline_trades if baseline_trades > 0 else 0.0,
            "max_dd_delta": max_drawdown - baseline_max_dd,
        },
        "probabilities": {
            "min": float(probs.min()),
            "max": float(probs.max()),
            "mean": float(probs.mean()),
            "median": float(np.median(probs)),
        },
        "sample_trades": outcomes[:5],  # First 5 trades for inspection
    }


def sweep_thresholds(events: list[dict[str, Any]], model: dict[str, Any], 
                     horizon: str = "30m") -> list[dict[str, Any]]:
    """Sweep different threshold values."""
    results = []
    thresholds = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    
    for t in thresholds:
        r = run_backtest(events, model, t, horizon)
        if "error" not in r:
            results.append({
                "threshold": t,
                "n_trades": r["meta_filtered"]["n_trades"],
                "win_rate": r["meta_filtered"]["win_rate"],
                "avg_return": r["meta_filtered"]["avg_return"],
                "max_drawdown": r["meta_filtered"]["max_drawdown"],
                "win_rate_delta": r["improvement"]["win_rate_delta"],
            })
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Run backtest harness for meta-labeling")
    parser.add_argument("--input-events", "-i", 
                        default="dev/meta_labeling/out/labeled_events.jsonl",
                        help="Input labeled events JSONL")
    parser.add_argument("--input-model", "-m",
                        default="dev/meta_labeling/out/model_weights.json",
                        help="Input model weights JSON")
    parser.add_argument("--out", "-o",
                        default="dev/meta_labeling/out/backtest_results.json",
                        help="Output backtest results JSON")
    parser.add_argument("--threshold", "-t", type=float, default=0.5,
                        help="Probability threshold for meta-filter (default: 0.5)")
    parser.add_argument("--horizon", "-H", default="30m",
                        help="Label horizon (e.g., 30m, 60m)")
    parser.add_argument("--sweep", action="store_true",
                        help="Sweep multiple thresholds")
    
    args = parser.parse_args()
    
    # Load events
    print(f"Loading events from {args.input_events}...")
    events = load_labeled_events(args.input_events)
    print(f"Loaded {len(events)} events")
    
    # Filter to events with valid labels
    valid_events = [
        e for e in events 
        if e.get("labels", {}).get(f"label_{args.horizon}") is not None
    ]
    print(f"Valid events with {args.horizon} labels: {len(valid_events)}")
    
    # Load model
    print(f"Loading model from {args.input_model}...")
    model = load_model(args.input_model)
    print(f"Model features: {model.get('feature_names', [])[:5]}...")
    
    if args.sweep:
        # Sweep thresholds
        print("\n=== Threshold Sweep ===")
        results = sweep_thresholds(valid_events, model, args.horizon)
        
        print(f"{'Threshold':>10} | {'Trades':>6} | {'Win%':>6} | {'AvgRet':>8} | {'MaxDD':>8} | {'ΔWin%':>6}")
        print("-" * 60)
        for r in results:
            print(f"{r['threshold']:>10.1f} | {r['n_trades']:>6} | {r['win_rate']:>6.1%} | {r['avg_return']:>8.4f} | {r['max_drawdown']:>8.4f} | {r['win_rate_delta']:>+6.1%}")
        
        # Save sweep results
        with open(args.out, "w") as f:
            json.dump({"sweep": results}, f, indent=2)
        print(f"\nSweep results saved to {args.out}")
    else:
        # Single threshold backtest
        print(f"\n=== Backtest (threshold={args.threshold}) ===")
        result = run_backtest(valid_events, model, args.threshold, args.horizon)
        
        if "error" in result:
            print(f"⚠️  {result['error']}")
            with open(args.out, "w") as f:
                json.dump(result, f, indent=2)
            return
        
        # Print summary
        print(f"\n--- Baseline (all signals) ---")
        b = result["baseline"]
        print(f"  Trades: {b['n_trades']}, Wins: {b['n_wins']}, Win Rate: {b['win_rate']:.1%}")
        print(f"  Avg Return: {b['avg_return']:.4f}, Max DD: {b['max_drawdown']:.4f}")
        
        print(f"\n--- With Meta-Filter (threshold={args.threshold}) ---")
        m = result["meta_filtered"]
        print(f"  Trades: {m['n_trades']}, Wins: {m['n_wins']}, Win Rate: {m['win_rate']:.1%}")
        print(f"  Avg Return: {m['avg_return']:.4f}, Max DD: {m['max_drawdown']:.4f}")
        
        print(f"\n--- Improvement ---")
        imp = result["improvement"]
        print(f"  Win Rate Δ: {imp['win_rate_delta']:+.1%}")
        print(f"  Trade Reduction: {imp['trade_reduction_pct']:.1%}")
        print(f"  Max DD Δ: {imp['max_dd_delta']:+.4f}")
        
        print(f"\n--- Probability Distribution ---")
        prob = result["probabilities"]
        print(f"  Min: {prob['min']:.3f}, Max: {prob['max']:.3f}, Mean: {prob['mean']:.3f}, Median: {prob['median']:.3f}")
        
        # Save results
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nResults saved to {args.out}")


if __name__ == "__main__":
    main()
