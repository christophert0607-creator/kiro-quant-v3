# Stage 1: SQLAlchemy ORM
from self_learn.models import (
    init_db,
    save_prediction,
    log_signal,
    record_outcome,
    save_model_version,
    get_stats,
    get_open_signals,
    get_recent_outcomes,
    get_latest_prediction,
    get_latest_model_version,
    update_signal_status,
    session_scope,
    Prediction,
    Signal,
    Outcome,
    ModelVersion,
)
from self_learn.feedback import (
    on_trade_closed,
    check_and_retrain,
    should_retrain,
    hook_on_signal,
    hook_on_prediction,
)
from self_learn.retrain import (
    retrain_pipeline,
    load_latest_model,
    predict_profitable,
    build_training_data,
    get_latest_model_path,
)

__all__ = [
    # ORM
    "init_db",
    "save_prediction",
    "log_signal",
    "record_outcome",
    "save_model_version",
    "get_stats",
    "get_open_signals",
    "get_recent_outcomes",
    "get_latest_prediction",
    "get_latest_model_version",
    "update_signal_status",
    "session_scope",
    "Prediction",
    "Signal",
    "Outcome",
    "ModelVersion",
    # Feedback
    "on_trade_closed",
    "check_and_retrain",
    "should_retrain",
    "hook_on_signal",
    "hook_on_prediction",
    # Retrain (Stage 3)
    "retrain_pipeline",
    "load_latest_model",
    "predict_profitable",
    "build_training_data",
    "get_latest_model_path",
]
