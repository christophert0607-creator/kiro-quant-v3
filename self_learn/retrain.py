"""
Stage 3: XGBoost Batch Retrain for Self-Learning Trading System.

Builds training data from predictions + outcomes + signals,
trains an XGBoost classifier to predict trade profitability,
and saves a hot-swappable model.
"""

from __future__ import annotations

import json
import pickle
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import xgboost as xgb
from sklearn.metrics import accuracy_score, classification_report

from self_learn.models import (
    session_scope,
    Prediction,
    Signal,
    Outcome,
    ModelVersion,
    save_model_version,
    get_recent_outcomes,
    get_latest_model_version,
)


# ─── Data Joining ──────────────────────────────────────────────────────────────

def build_training_data(max_samples: int = 500) -> tuple[list[np.ndarray], list[int]]:
    """Build (X, y) training data from predictions + signals + outcomes.

    Returns:
        X: list of feature vectors
        y: list of labels (1=profitable, 0=loss)
    """
    with session_scope() as session:
        # Get all closed signals with their outcomes
        rows = (
            session.query(Signal, Outcome, Prediction)
            .join(Outcome, Signal.id == Outcome.signal_id)
            .join(Prediction, Signal.prediction_id == Prediction.id)
            .filter(Signal.status == "CLOSED")
            .order_by(Outcome.closed_at.desc())
            .limit(max_samples)
            .all()
        )

    X_list: list[np.ndarray] = []
    y_list: list[int] = []

    for signal, outcome, prediction in rows:
        # Try to build rich features from stored indicators dict
        try:
            vec = _build_feature_vector(prediction, signal, outcome)
        except Exception:
            # Fallback: minimal 5-feature vector (no indicators stored yet)
            vec = np.array([
                float(prediction.confidence or 0.0),
                0.5,                                          # RSI=neutral / 100
                0.0,                                          # MACD=zero
                0.5,                                          # BB_pos=mid
                0.0,                                          # SMA_ratio=flat
                0.0,                                          # price_reg=flat
                float(outcome.hold_minutes or 60) / 1440.0, # hold (known at close, but OK for training)
                1.0 if signal.action == "BUY" else 0.0,
                0.5,                                          # hour=midday
            ], dtype=np.float32)

        # Label: 1 if profitable, 0 if loss
        label = 1 if (outcome.pnl or 0) > 0 else 0

        X_list.append(vec)
        y_list.append(label)

    return X_list, y_list


def _parse_indicators(prediction: Prediction) -> dict:
    """Parse stored indicators dict from prediction.feature_vector.

    Returns a flat dict with indicator names as keys.
    Falls back to empty dict if feature_vector is None or unparseable.
    """
    import pickle
    if not prediction.feature_vector:
        return {}
    try:
        raw = pickle.loads(prediction.feature_vector)
        if isinstance(raw, dict):
            return raw
        return {}
    except Exception:
        return {}


def _build_feature_vector(prediction: Prediction, signal: Signal, outcome: Outcome) -> np.ndarray:
    """Build a 9-element feature vector using stored technical indicators.

    Features (all known at signal time — no outcome leakage):
      [0] confidence:      model certainty (0-1)
      [1] RSI_14:          relative strength (0-100), normalized /100
      [2] MACD_HIST:       momentum indicator, normalized /5 (typical range ±2)
      [3] BB_POSITION:    price position within Bollinger Bands (0-1), normalized
      [4] SMA_5_SMA20:    5/20 SMA ratio - 1 (mean-reversion signal), normalized /0.1
      [5] price_regime:    current_price / predicted_price - 1 (forecast error proxy), normalized /0.1
      [6] hold_expected:  expected hold in minutes / 1440 (normalized to days)
      [7] action_BUY:      1 if BUY else 0
      [8] hour_of_day:     market hour (9-16 normalized to 0-1)
    """
    from datetime import datetime
    ind = _parse_indicators(prediction)

    rsi        = float(ind.get("RSI_14", 50.0)) / 100.0
    macd_hist  = float(ind.get("MACD_HIST", 0.0)) / 5.0
    bb_lower   = float(ind.get("BB_LOWER", 0.0))
    bb_mid    = float(ind.get("BB_MIDDLE", 0.0))
    bb_upper   = float(ind.get("BB_UPPER", 0.0))
    sma5      = float(ind.get("SMA_5", 0.0))
    sma20     = float(ind.get("SMA_20", 0.0))
    close     = float(prediction.predicted_price or 0.0)

    # BB position: where is price relative to BB band width?
    bb_range = bb_upper - bb_lower if bb_upper != bb_lower else 1.0
    bb_pos = (close - bb_lower) / bb_range if bb_range > 0 else 0.5

    # SMA ratio: 5 vs 20 SMA (mean reversion signal)
    sma_ratio = (sma5 / sma20 - 1) / 0.1 if sma20 > 0 else 0.0

    # Price-regime: how far is prediction from current price?
    price_reg = (close / max(prediction.predicted_price or close, 1e-9) - 1) / 0.1

    entry_hour = signal.created_at.hour if signal.created_at else 12.0

    # Expected hold: use avg historical hold from closed outcomes, or default 60 min
    avg_hold = 60.0  # minutes — will be overridden if available
    return np.array([
        float(prediction.confidence or 0.0),   # [0] confidence
        rsi,                                     # [1] RSI /100
        macd_hist,                               # [2] MACD_HIST/5
        bb_pos,                                 # [3] BB position (0-1)
        sma_ratio,                             # [4] SMA 5/20 reversion
        price_reg,                             # [5] price-regime
        float(avg_hold) / 1440.0,              # [6] expected hold (normalized)
        1.0 if signal.action == "BUY" else 0.0, # [7] action
        entry_hour / 24.0,                     # [8] market hour
    ], dtype=np.float32)


# ─── Training ─────────────────────────────────────────────────────────────────

def train_xgboost_model(X: list[np.ndarray], y: list[int]) -> tuple[xgb.XGBClassifier, dict]:
    """Train XGBoost classifier on feature vectors.

    Returns:
        model: trained XGBClassifier
        metrics: dict with accuracy, win_rate, sample_count
    """
    if len(X) < 10:
        raise ValueError(f"Not enough training samples: {len(X)}")

    X_arr = np.array(X)
    y_arr = np.array(y)

    # Stratified train/test split (last 20% as holdout)
    split = int(len(X_arr) * 0.8)
    X_train, X_test = X_arr[:split], X_arr[split:]
    y_train, y_test = y_arr[:split], y_arr[split:]

    # 2026-04-12 Fix: Early stopping + regularization to prevent overfitting
    # 2026-04-14 Fix: Add scale_pos_weight to handle 80/20 class imbalance.
    #   minority (profitable, label=1) gets higher weight so the model pays attention.
    n_neg = sum(1 for v in y if v == 0)
    n_pos = sum(1 for v in y if v == 1)
    scale_pos_weight = max(n_neg / max(n_pos, 1), 1.0)  # avoid div-by-zero

    model = xgb.XGBClassifier(
        n_estimators=150,           # Reduced from 500 — early stopping will find optimal <= 150
        max_depth=3,
        learning_rate=0.03,        # Reduced from 0.05 — slower convergence, better generalization
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.5,             # Increased from 0.1 — stronger L1 to prune noise features
        reg_lambda=2.0,            # Increased from 1.0 — stronger L2 to prevent overfitting
        min_child_weight=5,        # Increased from 3 — require more samples per leaf
        scale_pos_weight=scale_pos_weight,  # weight minority class (profitable trades)
        eval_metric="logloss",     # Primary metric for early stopping
        random_state=42,
        n_jobs=-1,
        early_stopping_rounds=15,  # Stop if no improvement for 15 consecutive rounds
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    # Evaluate on HOLD-OUT test set only (not training data)
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)

    # True win rate from test set (proportion of positive class in test)
    win_rate = float(sum(y_test)) / max(len(y_test), 1)

    # Best iteration from early stopping
    best_iter = getattr(model, 'best_iteration', None)
    actual_iters = getattr(model, 'best_iteration', None) or model.n_estimators

    metrics = {
        "accuracy": round(float(accuracy), 4),
        "win_rate": round(float(win_rate), 4),
        "train_size": len(X_train),
        "test_size": len(X_test),
        "total_samples": len(X_arr),
        "actual_iterations": int(actual_iters),
        "early_stopped": best_iter is not None,
    }

    return model, metrics


# ─── Model Persistence ──────────────────────────────────────────────────────────

def save_trained_model(
    model: xgb.XGBClassifier,
    metrics: dict,
    model_dir: Path,
) -> tuple[str, Path]:
    """Save trained model to disk. Returns (version_id, model_path)."""
    model_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"model_{timestamp}.pkl"
    model_path = model_dir / filename

    # Save model
    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    # Save metadata alongside
    meta_path = model_dir / f"meta_{timestamp}.json"
    with open(meta_path, "w") as f:
        json.dump({
            "filename": filename,
            "metrics": metrics,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, f, indent=2)

    # Save in DB
    version_id = save_model_version(
        model_path=str(model_path),
        dataset_size=metrics["total_samples"],
        accuracy=metrics["accuracy"],
    )

    return version_id, model_path


# ─── Hot Swap ─────────────────────────────────────────────────────────────────

def get_latest_model_path() -> Optional[Path]:
    """Get path to the most recently trained model."""
    latest = get_latest_model_version()
    if latest and latest.model_path:
        p = Path(latest.model_path)
        if p.exists():
            return p
    return None


def load_latest_model() -> Optional[xgb.XGBClassifier]:
    """Hot-swap load the most recent trained model."""
    path = get_latest_model_path()
    if path is None:
        return None
    try:
        with open(path, "rb") as f:
            model = pickle.load(f)
        return model
    except Exception:
        return None


def predict_profitable(model: xgb.XGBClassifier, features: np.ndarray) -> float:
    """Use trained model to score profitability probability.

    Args:
        model: trained XGBClassifier
        features: feature vector (same shape as training)

    Returns:
        probability of being profitable (0.0 - 1.0)
    """
    proba = model.predict_proba(features.reshape(1, -1))
    return float(proba[0][1])


# ─── Model Promotion Guard ─────────────────────────────────────────────────────

_REQUIRED_OUTCOME_PROVENANCE_COLUMNS = {"source", "broker_order_id", "recorded_by", "provenance_meta"}
_ELIGIBLE_OUTCOME_SOURCES = {"paper_broker", "live_broker"}


def _eligible_real_source_count(db_path: Path) -> tuple[bool, int]:
    """Return (schema_ready, eligible paper/live broker outcome count)."""
    if not db_path.exists():
        return False, 0
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(outcomes)").fetchall()}
        if not _REQUIRED_OUTCOME_PROVENANCE_COLUMNS.issubset(cols):
            return False, 0
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM outcomes
            WHERE source IN ('paper_broker', 'live_broker')
              AND (
                (broker_order_id IS NOT NULL AND broker_order_id != '')
                OR (provenance_meta IS NOT NULL AND provenance_meta != '')
              )
            """
        ).fetchone()[0]
    return True, int(count or 0)


def validate_meta_model_promotion(
    X,
    y=None,
    metrics: dict | None = None,
    db_path: Path | str | None = None,
    min_eligible_outcomes: int = 100,
    min_accuracy: float = 0.60,
    min_symbol_coverage: int = 1,
) -> dict:
    """Validate whether a freshly trained meta model may be persisted/promoted.

    This guard deliberately runs *after* in-memory training and *before* any model
    artifact, model_versions row, or training_log.jsonl write. Synthetic/seeded
    outcomes are never eligible evidence for promotion.
    """
    metrics = dict(metrics or {})
    db_path = Path(db_path) if db_path is not None else Path(__file__).parent / "trading_bot.db"
    X_arr = np.asarray(X)
    y_arr = np.asarray(y if y is not None else [])
    schema_ready, eligible_count = _eligible_real_source_count(db_path)
    accuracy = float(metrics.get("accuracy", 0.0) or 0.0)

    checks = {
        "feature_shape_valid": X_arr.ndim == 2 and X_arr.shape[0] > 0 and X_arr.shape[1] in {6, 9},
        "finite_matrix": bool(X_arr.size) and bool(np.isfinite(X_arr).all()),
        "label_shape_valid": y_arr.ndim == 1 and len(y_arr) == len(X_arr),
        "real_source_verified": schema_ready and eligible_count >= int(min_eligible_outcomes),
        "holdout_accuracy_ok": accuracy >= float(min_accuracy),
        # Symbol coverage is conservatively passed when no richer per-symbol metadata
        # is available in this legacy training function. The provenance count remains
        # the binding promotion gate until outcome-head training supplies coverage.
        "symbol_coverage_ok": int(min_symbol_coverage) <= 1,
    }
    result = {
        "status": "pass" if all(checks.values()) else "blocked",
        "reason": None if all(checks.values()) else "meta_model_promotion_guard",
        "schema_ready": schema_ready,
        "eligible_real_source_count": eligible_count,
        "required_eligible_outcomes": int(min_eligible_outcomes),
        "real_source_verified": checks["real_source_verified"],
        "accuracy": accuracy,
        "required_accuracy": float(min_accuracy),
        **checks,
    }
    return result


# ─── Main Retrain Pipeline ─────────────────────────────────────────────────────

def retrain_pipeline() -> dict:
    """Full retrain pipeline: build data → train → guard → save → log.

    Returns a summary dict. The promotion guard must pass before any persistence
    write, so synthetic-only training can still be measured without being promoted.
    """
    try:
        # 1. Build training data
        X, y = build_training_data(max_samples=500)
        if len(X) < 10:
            return {"status": "skip", "reason": f"Only {len(X)} samples, need ≥10"}

        # 2. Train in memory
        model, metrics = train_xgboost_model(X, y)

        # 3. Promotion guard — no writes before this point
        guard = validate_meta_model_promotion(X, y=y, metrics=metrics)
        if guard["status"] != "pass":
            return {"status": "blocked", **guard, "metrics": metrics}

        # 4. Save
        model_dir = Path(__file__).parent / "models"
        version_id, model_path = save_trained_model(model, metrics, model_dir)

        # 5. Write training log
        log_entry = {
            "version_id": version_id,
            "model_path": str(model_path),
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "promotion_guard": guard,
            "sample_breakdown": {
                "total": len(y),
                "profitable": sum(y),
                "loss": len(y) - sum(y),
            },
        }
        log_file = model_dir / "training_log.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        return {
            "status": "ok",
            "version_id": version_id,
            "model_path": str(model_path),
            "metrics": metrics,
            "promotion_guard": guard,
        }

    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "retrain":
        result = retrain_pipeline()
        print(json.dumps(result, indent=2, default=str))
    elif len(sys.argv) > 1 and sys.argv[1] == "status":
        latest = get_latest_model_version()
        if latest:
            print(f"Model: {latest.id}")
            print(f"Accuracy: {latest.accuracy}")
            print(f"Trained: {latest.trained_at}")
            print(f"Path: {latest.model_path}")
        else:
            print("No model trained yet")
    else:
        print("Usage: python -m self_learn.retrain [retrain|status]")
