import os

"""Feedback Loop for Self-Learning Trading System.

Triggers:
    - on_trade_closed(signal_id, exit_price, pnl) → writes outcome → check retrain
    - should_retrain() → checks N outcomes or time threshold
    - retrain_model() → batch train with new data
    - hot_swap_model(new_version_id) → load new model into LiveTradingLoop

Usage:
    from self_learn.feedback import on_trade_closed, check_and_retrain

    # After a position closes:
    on_trade_closed(signal_id="xxx", exit_price=165.5, pnl=120.0, pnl_pct=0.73)

    # In cron job (every 4 hours):
    check_and_retrain()
"""

import json
import pickle
import shutil
from pathlib import Path
from datetime import datetime, timezone
from self_learn.models import (
    record_outcome as _record_outcome,
    save_model_version,
    get_latest_model_version,
    get_stats,
    get_open_signals,
    get_recent_outcomes,
)
from self_learn.config import (
    RETRAIN_MIN_OUTCOMES,
    RETRAIN_INTERVAL_HOURS,
    MODEL_DIR,
    RETRAIN_BATCH_SIZE,
)

# ─── Config ────────────────────────────────────────────────────────────────────
from self_learn.config import (
    RETRAIN_MIN_OUTCOMES,
    RETRAIN_INTERVAL_HOURS,
    MODEL_DIR,
    RETRAIN_BATCH_SIZE,
)

LATEST_OUTCOME_FILE = Path(__file__).parent / "latest_outcome.json"
RETRAIN_LOCK_FILE = Path(__file__).parent / ".retrain_lock"


# ─── Core Feedback ────────────────────────────────────────────────────────────

def on_trade_closed(
    signal_id: str,
    exit_price: float,
    pnl: float,
    pnl_pct: float,
    hold_minutes: int,
    prediction_error: float | None = None,
    source: str = "synthetic_seed",
    broker_order_id: str | None = None,
    recorded_by: str | None = None,
    provenance_meta: str | None = None,
) -> dict:
    """Called when a position is closed. Writes outcome to DB."""
    _record_outcome(
        signal_id=signal_id,
        exit_price=exit_price,
        pnl=pnl,
        pnl_pct=pnl_pct,
        hold_minutes=hold_minutes,
        prediction_error=prediction_error,
        source=source,
        broker_order_id=broker_order_id,
        recorded_by=recorded_by,
        provenance_meta=provenance_meta,
    )
    # Write latest outcome for quick review
    latest = {
        "signal_id": signal_id,
        "exit_price": exit_price,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "hold_minutes": hold_minutes,
        "prediction_error": prediction_error,
        "source": source,
        "broker_order_id": broker_order_id,
        "recorded_by": recorded_by,
        "closed_at": datetime.now(timezone.utc).isoformat(),
    }
    LATEST_OUTCOME_FILE.write_text(json.dumps(latest, indent=2))
    return latest


def get_outcome_count() -> int:
    """Get count of closed outcomes in DB."""
    stats = get_stats()
    return stats["closed_signals"]


# Kill-switch: set SELF_LEARN_ENABLED=0 in .env to disable auto-retrain entirely
_SELF_LEARN_ENABLED = os.getenv("SELF_LEARN_ENABLED", "1") == "1"

def should_retrain() -> bool:
    """Check if model should be retrained."""
    if not _SELF_LEARN_ENABLED:
        return False
    latest_version = get_latest_model_version()
    outcome_count = get_outcome_count()

    # Not enough outcomes yet
    if outcome_count < RETRAIN_MIN_OUTCOMES:
        return False

    # No previous model — always retrain first time
    if latest_version is None:
        return True

    # Check time since last training
    last_trained = latest_version.trained_at.replace(tzinfo=timezone.utc)
    hours_since = (datetime.now(timezone.utc) - last_trained).total_seconds() / 3600

    return hours_since >= RETRAIN_INTERVAL_HOURS


def retrain_model() -> dict | None:
    """Retrain model with accumulated outcomes. Returns new version info."""
    outcomes = get_recent_outcomes(limit=RETRAIN_BATCH_SIZE)
    if len(outcomes) < RETRAIN_MIN_OUTCOMES:
        return None

    # TODO: Implement actual model training
    # For now, save a training marker
    model_dir = MODEL_DIR
    model_dir.mkdir(parents=True, exist_ok=True)

    version_id = save_model_version(
        model_path=str(model_dir / f"model_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pkl"),
        dataset_size=len(outcomes),
        accuracy=0.0,  # TODO: calculate actual accuracy
    )

    # Write training log
    log = {
        "version_id": version_id,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "outcomes_used": len(outcomes),
        "avg_pnl": sum(o["pnl"] for o in outcomes) / len(outcomes),
        "win_rate": len([o for o in outcomes if o["pnl"] > 0]) / len(outcomes),
    }
    log_file = model_dir / "training_log.json"
    log_file.write_text(json.dumps(log, indent=2))

    return log


def check_and_retrain() -> dict:
    """Main entry point for cron job. Checks and retrains if needed."""
    stats = get_stats()
    can_retrain = should_retrain()

    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "should_retrain": can_retrain,
        "retrain_done": False,
        "new_version": None,
    }

    if can_retrain:
        # Lock to prevent concurrent retraining
        if RETRAIN_LOCK_FILE.exists():
            lock_time = datetime.fromtimestamp(RETRAIN_LOCK_FILE.stat().st_mtime)
            if (datetime.now() - lock_time).total_seconds() < 3600:
                result["skipped"] = "Retrain already in progress"
                return result
        RETRAIN_LOCK_FILE.write_text(datetime.now().isoformat())

        new_version = retrain_model()
        result["retrain_done"] = True
        result["new_version"] = new_version

        if RETRAIN_LOCK_FILE.exists():
            RETRAIN_LOCK_FILE.unlink()

    return result


def write_progress(stage: str, step: str, detail: str = "") -> None:
    """Write development progress to file for cron job to review."""
    progress_file = Path(__file__).parent / "PROGRESS.md"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"\n## [{timestamp}] Stage: {stage} | Step: {step}\n{detail}\n"
    existing = progress_file.read_text() if progress_file.exists() else "# Development Progress\n"
    progress_file.write_text(existing + entry)


# ─── Integration Hooks ───────────────────────────────────────────────────────

def hook_on_signal(
    action: str,
    entry_price: float,
    size: int,
    prediction_id: str | None = None,
    signal_id: str | None = None,
) -> str:
    """Called when a new trading signal is generated. Returns the signal_id."""
    from self_learn.models import log_signal
    sig_id = log_signal(
        action=action,
        prediction_id=prediction_id,
        entry_price=entry_price,
        size=size,
        status="OPEN",
    )
    return sig_id


def hook_on_prediction(
    symbol: str,
    predicted_price: float,
    confidence: float,
    indicators: dict | None = None,
) -> str:
    """Called when ML model makes a prediction. Returns the prediction_id.

    Args:
        symbol: Stock ticker
        predicted_price: ML predicted price
        confidence: Confidence score (0-1)
        indicators: Dict of technical indicators at signal time, e.g.
            {"RSI_14": 28.5, "MACD_HIST": 0.15, "BB_LOWER": 180.0, ...}
            These are stored as feature_vector and used in training.
    """
    from self_learn.models import save_prediction
    import pickle

    # Serialize indicators dict to bytes for storage
    feature_vector: bytes | None = None
    if indicators:
        feature_vector = pickle.dumps(indicators)

    return save_prediction(
        symbol=symbol,
        predicted_price=predicted_price,
        confidence=confidence,
        feature_vector=feature_vector,
    )


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        stats = get_stats()
        print(json.dumps(stats, indent=2, default=str))
    elif len(sys.argv) > 1 and sys.argv[1] == "retrain":
        result = check_and_retrain()
        print(json.dumps(result, indent=2, default=str))
    elif len(sys.argv) > 1 and sys.argv[1] == "outcomes":
        outcomes = get_recent_outcomes(limit=20)
        for o in outcomes:
            print(json.dumps(o, default=str))
    else:
        print("Usage: python -m self_learn.feedback [status|retrain|outcomes]")
