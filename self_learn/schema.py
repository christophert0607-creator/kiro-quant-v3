"""SQL Schema for Self-Learning Trading System.

Tables:
    - predictions: ML model outputs
    - signals: Trading signals (BUY/SELL/HOLD)
    - outcomes: Closed trade results with P&L
    - model_versions: Model version tracking
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
import uuid

DB_PATH = Path(__file__).parent / "self_learn.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    predicted_price REAL,
    confidence REAL,
    feature_vector BLOB,
    model_version_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    prediction_id TEXT REFERENCES predictions(id),
    action TEXT NOT NULL,
    entry_price REAL,
    size INTEGER,
    status TEXT DEFAULT 'OPEN',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS outcomes (
    signal_id TEXT PRIMARY KEY REFERENCES signals(id),
    exit_price REAL,
    pnl REAL,
    pnl_pct REAL,
    hold_minutes INTEGER,
    prediction_error REAL,
    closed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_versions (
    id TEXT PRIMARY KEY,
    model_path TEXT NOT NULL,
    dataset_size INTEGER,
    accuracy REAL,
    trained_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_predictions_symbol ON predictions(symbol);
CREATE INDEX IF NOT EXISTS idx_predictions_created ON predictions(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_action ON signals(action);
CREATE INDEX IF NOT EXISTS idx_outcomes_closed ON outcomes(closed_at);
"""


def init_db(db_path: Path = DB_PATH) -> None:
    """Initialize the database with schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_connection(db_path: Path = DB_PATH):
    """Context manager for database connections."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ─── Predictions ──────────────────────────────────────────────────────────────

def write_prediction(
    symbol: str,
    predicted_price: float,
    confidence: float,
    feature_vector: bytes | None = None,
    model_version_id: str | None = None,
) -> str:
    """Write a prediction record. Returns the prediction ID."""
    pred_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO predictions (id, symbol, predicted_price, confidence, feature_vector, model_version_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pred_id, symbol, predicted_price, confidence, feature_vector, model_version_id),
        )
    return pred_id


def get_latest_prediction(symbol: str, limit: int = 1) -> list[dict]:
    """Get latest predictions for a symbol."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM predictions WHERE symbol = ? ORDER BY created_at DESC LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Signals ─────────────────────────────────────────────────────────────────

def write_signal(
    action: str,
    prediction_id: str | None = None,
    entry_price: float | None = None,
    size: int | None = None,
    status: str = "OPEN",
) -> str:
    """Write a signal record. Returns the signal ID."""
    signal_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO signals (id, prediction_id, action, entry_price, size, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (signal_id, prediction_id, action, entry_price, size, status),
        )
    return signal_id


def update_signal_status(signal_id: str, status: str) -> None:
    """Update signal status (OPEN → CLOSED)."""
    with get_connection() as conn:
        conn.execute("UPDATE signals SET status = ? WHERE id = ?", (status, signal_id))


def get_open_signals() -> list[dict]:
    """Get all open signals."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM signals WHERE status = 'OPEN' ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Outcomes ────────────────────────────────────────────────────────────────

def write_outcome(
    signal_id: str,
    exit_price: float,
    pnl: float,
    pnl_pct: float,
    hold_minutes: int,
) -> None:
    """Write an outcome for a closed signal."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO outcomes (signal_id, exit_price, pnl, pnl_pct, hold_minutes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (signal_id, exit_price, pnl, pnl_pct, hold_minutes),
        )


def get_recent_outcomes(limit: int = 50) -> list[dict]:
    """Get recent closed outcomes."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT o.*, s.symbol, s.action, s.entry_price, s.created_at as opened_at
            FROM outcomes o
            JOIN signals s ON o.signal_id = s.id
            ORDER BY o.closed_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Model Versions ──────────────────────────────────────────────────────────

def write_model_version(model_path: str, dataset_size: int, accuracy: float) -> str:
    """Write a new model version. Returns the version ID."""
    version_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO model_versions (id, model_path, dataset_size, accuracy)
            VALUES (?, ?, ?, ?)
            """,
            (version_id, model_path, dataset_size, accuracy),
        )
    return version_id


def get_latest_model_version() -> dict | None:
    """Get the most recently trained model version."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM model_versions ORDER BY trained_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


# ─── Stats ──────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    """Get overall statistics for the self-learning system."""
    with get_connection() as conn:
        total_predictions = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        total_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        open_signals = conn.execute("SELECT COUNT(*) FROM signals WHERE status='OPEN'").fetchone()[0]
        closed_signals = conn.execute("SELECT COUNT(*) FROM signals WHERE status='CLOSED'").fetchone()[0]
        total_outcomes = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]

        pnl_row = conn.execute(
            "SELECT SUM(pnl), AVG(pnl_pct), COUNT(*) FROM outcomes"
        ).fetchone()
        total_pnl = pnl_row[0] or 0.0
        avg_pnl_pct = pnl_row[1] or 0.0

        model_row = conn.execute(
            "SELECT id, accuracy, trained_at FROM model_versions ORDER BY trained_at DESC LIMIT 1"
        ).fetchone()
        current_model = dict(model_row) if model_row else None

    return {
        "total_predictions": total_predictions,
        "total_signals": total_signals,
        "open_signals": open_signals,
        "closed_signals": closed_signals,
        "total_outcomes": total_outcomes,
        "total_pnl": round(total_pnl, 4),
        "avg_pnl_pct": round(avg_pnl_pct, 4),
        "current_model_version": current_model,
    }
