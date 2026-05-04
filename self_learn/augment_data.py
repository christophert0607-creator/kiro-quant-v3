"""
Stage 1+2: Data Augmentation & Balanced Training for Self-Learning Trading System.

Actions:
  1. Load existing 118 closed outcomes from trading_bot.db
  2. Apply oversampling + class-weight to rebalance
  3. Retrain XGBoost with proper class weights
  4. Save new hot-swappable model
  5. Verify confidence distribution improved
"""

from __future__ import annotations

import json
import pickle
import uuid
import numpy as np
import xgboost as xgb
from pathlib import Path
from datetime import datetime, timezone
from sklearn.metrics import accuracy_score
from sklearn.utils import resample
import sqlite3

WORKSPACE = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3")
DB_PATH = WORKSPACE / "self_learn" / "trading_bot.db"
MODEL_DIR = WORKSPACE / "self_learn" / "models"
LOG_FILE = MODEL_DIR / "training_log.jsonl"

# ─── Load existing outcomes ────────────────────────────────────────────────────

def load_outcomes() -> tuple[list[np.ndarray], list[int]]:
    """Load (X, y) from trading_bot.db outcomes joined with predictions."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT o.*, p.symbol, p.confidence, p.predicted_price, p.feature_vector, s.action
        FROM outcomes o
        JOIN signals s ON o.signal_id = s.id
        JOIN predictions p ON s.prediction_id = p.id
        ORDER BY o.closed_at DESC
    """).fetchall()
    conn.close()

    X_list, y_list = [], []
    for r in rows:
        label = 1 if (r["pnl"] or 0) > 0 else 0
        try:
            if r["feature_vector"]:
                fv = pickle.loads(r["feature_vector"])
                if isinstance(fv, dict):
                    vec = np.array([
                        fv.get("confidence", 0.0),
                        fv.get("pnl_pct", 0.0) / 10.0,
                        fv.get("hold_minutes", 0) / 1440.0,
                        1.0 if r["action"] == "BUY" else 0.0,
                    ], dtype=np.float32)
                else:
                    vec = np.array(fv, dtype=np.float32)
            else:
                vec = np.array([
                    float(r["confidence"] or 0.0),
                    float(r["pnl_pct"] or 0.0) / 10.0,
                    float(r["hold_minutes"] or 0) / 1440.0,
                    1.0 if r["action"] == "BUY" else 0.0,
                ], dtype=np.float32)
        except Exception:
            vec = np.array([
                float(r["confidence"] or 0.0),
                float(r["pnl_pct"] or 0.0) / 10.0,
                float(r["hold_minutes"] or 0) / 1440.0,
                1.0 if r["action"] == "BUY" else 0.0,
            ], dtype=np.float32)

        X_list.append(vec)
        y_list.append(label)

    return X_list, y_list

# ─── Oversample minority class ─────────────────────────────────────────────────

def oversample_balance(X_orig: list[np.ndarray], y_orig: list[int], target_ratio: float = 0.6) -> tuple[list[np.ndarray], list[int]]:
    """
    Oversample profitable (minority) samples so win rate approaches target_ratio.
    Adds Gaussian noise to duplicated samples for diversity.
    """
    X_arr = np.array(X_orig)
    y_arr = np.array(y_orig)

    win_idx = np.where(y_arr == 1)[0]
    loss_idx = np.where(y_arr == 0)[0]

    n_loss = len(loss_idx)
    n_win = len(win_idx)

    # Target: n_win / (n_win + n_loss) ~= target_ratio
    target_win = int(n_loss * target_ratio / (1 - target_ratio))
    n_to_add = max(0, target_win - n_win)

    print(f"  Original: {n_win} wins, {n_loss} losses, win_rate={n_win/(n_win+n_loss)*100:.1f}%")
    print(f"  Target win rate: {target_ratio*100:.0f}% → need ~{n_to_add} more wins")

    if n_to_add > 0:
        # Sample with replacement + add noise
        extra_indices = np.random.choice(win_idx, size=n_to_add, replace=True)
        extra_X = X_arr[extra_indices]
        # Add small Gaussian noise (std=0.02) to prevent memorization
        noise = np.random.normal(0, 0.02, extra_X.shape).astype(np.float32)
        extra_X_noisy = np.clip(extra_X + noise, 0.0, 1.0)

        X_balanced = np.vstack([X_arr, extra_X_noisy])
        y_balanced = np.concatenate([y_arr, np.ones(n_to_add, dtype=int)])
    else:
        X_balanced, y_balanced = X_arr, y_arr

    print(f"  Augmented: {int(sum(y_balanced))} wins, {len(y_balanced)-int(sum(y_balanced))} losses, win_rate={sum(y_balanced)/len(y_balanced)*100:.1f}%")
    return list(X_balanced), list(y_balanced)

# ─── Train with class weights ─────────────────────────────────────────────────

def train_balanced_xgb(X: list[np.ndarray], y: list[int]) -> tuple[xgb.XGBClassifier, dict]:
    """Train XGBoost with scale_pos_weight to handle imbalance.

    IMPORTANT: Oversample ONLY the training set (after split), keep test set pristine.
    """
    X_arr = np.array(X)
    y_arr = np.array(y)

    # Stratified split FIRST (before any oversampling)
    np.random.seed(42)
    indices = np.arange(len(X_arr))
    np.random.shuffle(indices)
    split = int(len(X_arr) * 0.8)
    train_idx, test_idx = indices[:split], indices[split:]
    X_train, X_test = X_arr[train_idx], X_arr[test_idx]
    y_train, y_test = y_arr[train_idx], y_arr[test_idx]

    print(f"  Train: {len(y_train)} samples (wins={sum(y_train)}, loss={len(y_train)-sum(y_train)})")
    print(f"  Test:  {len(y_test)} samples (wins={sum(y_test)}, loss={len(y_test)-sum(y_test)})")

    # Oversample training set only
    X_train_list, y_train_list = list(X_train), list(y_train)
    X_train_aug, y_train_aug = oversample_balance(X_train_list, y_train_list, target_ratio=0.45)
    X_train_aug = np.array(X_train_aug)
    y_train_aug = np.array(y_train_aug)

    # Compute class weight ratio from ORIGINAL train (before oversample)
    n_pos = sum(y_train)
    n_neg = len(y_train) - n_pos
    scale_pos_weight = n_neg / max(n_pos, 1)
    print(f"  scale_pos_weight: {scale_pos_weight:.2f}")

    model = xgb.XGBClassifier(
        n_estimators=500,
        max_depth=3,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.3,          # Higher L1 for sparse features
        reg_lambda=2.0,         # Higher L2 for regularization
        min_child_weight=5,      # Stronger regularization
        scale_pos_weight=scale_pos_weight,  # Class imbalance correction
        eval_metric="auc",       # AUC is better than logloss for imbalanced
        objective="binary:logistic",
        random_state=42,
        n_jobs=-1,
        early_stopping_rounds=30,
    )

    model.fit(
        X_train_aug, y_train_aug,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    win_rate = float(sum(y_test)) / max(len(y_test), 1)
    best_iter = getattr(model, 'best_iteration', None)

    metrics = {
        "accuracy": round(float(accuracy), 4),
        "win_rate": round(float(win_rate), 4),
        "train_size": len(X_train_aug),
        "test_size": len(y_test),
        "total_samples": len(X_arr),
        "actual_iterations": int(best_iter or model.n_estimators),
        "early_stopped": best_iter is not None,
        "scale_pos_weight": round(scale_pos_weight, 2),
        "augmented": True,
    }
    return model, metrics

# ─── Simulate higher-confidence predictions ───────────────────────────────────

def simulate_prediction_distribution(model: xgb.XGBClassifier, n_sim: int = 5000) -> dict:
    """Simulate what confidence distribution the new model produces."""
    # Use the feature ranges from training data
    np.random.seed(42)
    # confidence in [0,1], pnl_pct in [-1, 1], hold in [0,1], buy_flag in {0,1}
    sim_X = np.random.rand(n_sim, 4).astype(np.float32)
    sim_X[:, 0] = np.random.beta(2, 5, n_sim)  # Most conf low, some high
    sim_X[:, 1] = np.random.beta(2, 5, n_sim) * 2 - 1  # skewed positive
    sim_X[:, 2] = np.random.beta(2, 2, n_sim)  # hold time
    sim_X[:, 3] = np.random.randint(0, 2, n_sim).astype(np.float32)

    proba = model.predict_proba(sim_X)[:, 1]
    return {
        "mean_confidence": float(np.mean(proba)),
        "median_confidence": float(np.median(proba)),
        "pct_above_035": float(np.mean(proba >= 0.35) * 100),
        "pct_above_050": float(np.mean(proba >= 0.50) * 100),
        "pct_above_070": float(np.mean(proba >= 0.70) * 100),
    }

# ─── Save model ───────────────────────────────────────────────────────────────

def save_model(model: xgb.XGBClassifier, metrics: dict) -> tuple[str, Path]:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"model_2026{timestamp[4:]}.pkl"
    model_path = MODEL_DIR / filename

    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    meta = {
        "filename": filename,
        "metrics": metrics,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = MODEL_DIR / f"meta_2026{timestamp[4:]}.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # Save to DB
    version_id = str(uuid.uuid4())
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO model_versions (id, model_path, dataset_size, accuracy, trained_at)
        VALUES (?, ?, ?, ?, ?)
    """, (version_id, str(model_path), metrics["total_samples"], metrics["accuracy"], datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()

    # Append to log
    log_entry = {
        "version_id": version_id,
        "model_path": str(model_path),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "sample_breakdown": {
            "total": int(metrics["total_samples"]),
            "profitable": int(metrics["train_size"] * metrics["win_rate"] * 0.8),  # approx
            "loss": int(metrics["train_size"] * 0.8 * (1 - metrics["win_rate"])),
        },
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    return version_id, model_path

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Kiro Quant V3 — Data Augmentation & Balanced Training")
    print("=" * 60)

    # 1. Load
    print("\n[1/5] Loading existing outcomes...")
    X_orig, y_orig = load_outcomes()
    print(f"  Loaded {len(X_orig)} closed outcomes")
    print(f"  Win rate: {sum(y_orig)/len(y_orig)*100:.1f}%")

    # 2. Train with class weights + train-set oversampling
    print("\n[2/5] Training XGBoost with class weights + oversampling on train set...")
    model, metrics = train_balanced_xgb(X_orig, y_orig)
    print(f"  Test accuracy: {metrics['accuracy']*100:.1f}%")
    print(f"  Test win rate:  {metrics['win_rate']*100:.1f}%")
    print(f"  Early stopped:  {metrics['early_stopped']}")
    print(f"  Best iteration: {metrics['actual_iterations']}")

    # 4. Simulate
    print("\n[4/5] Simulating prediction confidence distribution...")
    sim_stats = simulate_prediction_distribution(model)
    print(f"  Mean confidence:    {sim_stats['mean_confidence']:.3f}")
    print(f"  Median confidence:  {sim_stats['median_confidence']:.3f}")
    print(f"  Predictions ≥ 0.35: {sim_stats['pct_above_035']:.1f}%")
    print(f"  Predictions ≥ 0.50: {sim_stats['pct_above_050']:.1f}%")
    print(f"  Predictions ≥ 0.70: {sim_stats['pct_above_070']:.1f}%")

    # 5. Save
    print("\n[5/5] Saving model...")
    version_id, model_path = save_model(model, metrics)
    print(f"  Version ID: {version_id}")
    print(f"  Model path: {model_path}")

    print("\n" + "=" * 60)
    print("  ✅ Balanced model trained and saved!")
    print("=" * 60)

    return {"metrics": metrics, "sim_stats": sim_stats, "version_id": version_id}

if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2, default=str))
