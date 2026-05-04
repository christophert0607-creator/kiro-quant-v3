"""Configuration for Self-Learning Trading System.

These can be overridden via environment variables.
"""

from pathlib import Path

# Database
SELF_LEARN_DB = Path(__file__).parent / "self_learn.db"

# Retrain triggers
RETRAIN_MIN_OUTCOMES = int(__import__("os").getenv("RETRAIN_MIN_OUTCOMES", "100"))
RETRAIN_INTERVAL_HOURS = int(__import__("os").getenv("RETRAIN_INTERVAL_HOURS", "4"))
RETRAIN_BATCH_SIZE = int(__import__("os").getenv("RETRAIN_BATCH_SIZE", "500"))

# Model storage
MODEL_DIR = Path(__file__).parent / "models"
