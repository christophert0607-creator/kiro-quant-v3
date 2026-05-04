"""
Stage 1: SQLAlchemy ORM Models for Self-Learning Trading System.

Tables:
    - predictions: ML model outputs
    - signals: Trading signals (BUY/SELL/HOLD)
    - outcomes: Closed trade results with P&L
    - model_versions: Model version tracking

Author: Kiro Quant Agent
Date: 2026-03-31
"""

from __future__ import annotations

import uuid
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    String,
    Float,
    Integer,
    DateTime,
    LargeBinary,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    Session,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# ─── Database Setup ────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """SQLAlchemy Declarative Base."""
    pass


class Prediction(Base):
    """ML model prediction record."""
    __tablename__ = "predictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    predicted_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    feature_vector: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    model_version_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    signals: Mapped[list["Signal"]] = relationship(
        "Signal",
        back_populates="prediction",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_predictions_symbol", "symbol"),
        Index("idx_predictions_created", "created_at"),
    )


class Signal(Base):
    """Trading signal record."""
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    prediction_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("predictions.id"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="OPEN", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    prediction: Mapped[Optional["Prediction"]] = relationship(
        "Prediction",
        back_populates="signals",
        lazy="selectin",
    )
    outcome: Mapped[Optional["Outcome"]] = relationship(
        "Outcome",
        back_populates="signal",
        uselist=False,
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_signals_action", "action"),
        Index("idx_signals_status", "status"),
    )


class Outcome(Base):
    """Closed trade outcome record."""
    __tablename__ = "outcomes"

    signal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("signals.id"),
        primary_key=True,
    )
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hold_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prediction_error: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 2026-04-23: |pred - exit| for rolling MAE
    closed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    signal: Mapped["Signal"] = relationship(
        "Signal",
        back_populates="outcome",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_outcomes_closed", "closed_at"),
    )


class ModelVersion(Base):
    """Model version tracking record."""
    __tablename__ = "model_versions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    model_path: Mapped[str] = mapped_column(String(256), nullable=False)
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    dataset_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


# ─── Database Engine & Session ────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "trading_bot.db"
_engine = None
_SessionLocal = None


def get_engine():
    """Get or create the SQLAlchemy engine (singleton)."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            f"sqlite:///{DB_PATH}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
    return _engine


def get_session() -> Session:
    """Create a new database session."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()


def init_db() -> None:
    """Create all tables in the database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine())


# ─── Context Manager for Sessions ────────────────────────────────────────────

from contextlib import contextmanager


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations.

    Usage:
        with session_scope() as session:
            session.add(prediction)
    """
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ─── Write Functions ───────────────────────────────────────────────────────────

def save_prediction(
    symbol: str,
    predicted_price: Optional[float] = None,
    confidence: Optional[float] = None,
    feature_vector: Optional[bytes] = None,
    model_version_id: Optional[str] = None,
) -> str:
    """Save a prediction record. Returns the prediction ID."""
    if feature_vector is not None and not isinstance(feature_vector, bytes):
        feature_vector = pickle.dumps(feature_vector)

    pred = Prediction(
        symbol=symbol,
        predicted_price=predicted_price,
        confidence=confidence,
        feature_vector=feature_vector,
        model_version_id=model_version_id,
    )
    with session_scope() as session:
        session.add(pred)
    return pred.id


def log_signal(
    action: str,
    prediction_id: Optional[str] = None,
    entry_price: Optional[float] = None,
    size: Optional[int] = None,
    status: str = "OPEN",
) -> str:
    """Log a trading signal. Returns the signal ID."""
    signal = Signal(
        prediction_id=prediction_id,
        action=action,
        entry_price=entry_price,
        size=size,
        status=status,
    )
    with session_scope() as session:
        session.add(signal)
    return signal.id


def update_signal_status(signal_id: str, status: str) -> None:
    """Update the status of a signal (e.g., OPEN → CLOSED)."""
    with session_scope() as session:
        signal = session.query(Signal).filter_by(id=signal_id).first()
        if signal:
            signal.status = status


def record_outcome(
    signal_id: str,
    exit_price: Optional[float] = None,
    pnl: Optional[float] = None,
    pnl_pct: Optional[float] = None,
    hold_minutes: Optional[int] = None,
    prediction_error: Optional[float] = None,  # 2026-04-23: |pred_price - exit_price| for MAE
) -> None:
    """Record the outcome of a closed signal."""
    with session_scope() as session:
        outcome = Outcome(
            signal_id=signal_id,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            hold_minutes=hold_minutes,
            prediction_error=prediction_error,
        )
        # Also close the signal
        signal = session.query(Signal).filter_by(id=signal_id).first()
        if signal:
            signal.status = "CLOSED"
        session.add(outcome)


def save_model_version(
    model_path: str,
    dataset_size: Optional[int] = None,
    accuracy: Optional[float] = None,
) -> str:
    """Save a new model version. Returns the version ID."""
    version = ModelVersion(
        model_path=model_path,
        dataset_size=dataset_size,
        accuracy=accuracy,
    )
    with session_scope() as session:
        session.add(version)
    return version.id


# ─── Query Functions ────────────────────────────────────────────────────────────

def get_latest_prediction(symbol: str, limit: int = 1) -> list[Prediction]:
    """Get latest predictions for a symbol."""
    with session_scope() as session:
        return (
            session.query(Prediction)
            .filter_by(symbol=symbol)
            .order_by(Prediction.created_at.desc())
            .limit(limit)
            .all()
        )


def get_open_signals() -> list[Signal]:
    """Get all open signals."""
    with session_scope() as session:
        return session.query(Signal).filter_by(status="OPEN").all()


def get_recent_outcomes(limit: int = 50) -> list[Outcome]:
    """Get recent closed outcomes."""
    with session_scope() as session:
        return (
            session.query(Outcome)
            .order_by(Outcome.closed_at.desc())
            .limit(limit)
            .all()
        )


def get_latest_model_version() -> Optional[ModelVersion]:
    """Get the most recently trained model version."""
    with session_scope() as session:
        return (
            session.query(ModelVersion)
            .order_by(ModelVersion.trained_at.desc())
            .first()
        )


def get_stats() -> dict:
    """Get overall statistics for the self-learning system."""
    with session_scope() as session:
        total_predictions = session.query(Prediction).count()
        total_signals = session.query(Signal).count()
        open_signals = session.query(Signal).filter_by(status="OPEN").count()
        closed_signals = session.query(Signal).filter_by(status="CLOSED").count()
        total_outcomes = session.query(Outcome).count()

        outcome_stats = session.query(
            func.sum(Outcome.pnl),
            func.avg(Outcome.pnl_pct),
        ).first()

        total_pnl = outcome_stats[0] or 0.0
        avg_pnl_pct = outcome_stats[1] or 0.0

        current_model = (
            session.query(ModelVersion)
            .order_by(ModelVersion.trained_at.desc())
            .first()
        )

    return {
        "total_predictions": total_predictions,
        "total_signals": total_signals,
        "open_signals": open_signals,
        "closed_signals": closed_signals,
        "total_outcomes": total_outcomes,
        "total_pnl": round(total_pnl, 4),
        "avg_pnl_pct": round(avg_pnl_pct, 4),
        "current_model_version": (
            {
                "id": current_model.id,
                "accuracy": current_model.accuracy,
                "trained_at": current_model.trained_at.isoformat() if current_model else None,
            }
            if current_model
            else None
        ),
    }


def get_prediction_accuracy(symbol: str, window: int = 20) -> tuple[float, float]:
    """2026-04-23: Returns (mae, directional_accuracy) for recent closed trades.

    mae = mean(|predicted_price - exit_price|) over last `window` closed trades
    directional_accuracy = fraction where (pred > entry AND price went up) OR (pred < entry AND price went down)

    Returns (0.0, 0.5) if no outcomes available (neutral/default).
    """
    with session_scope() as s:
        # Join Outcome → Signal → Prediction to get predicted_price
        rows = (
            s.query(Outcome, Signal, Prediction)
            .join(Signal, Outcome.signal_id == Signal.id)
            .join(Prediction, Signal.prediction_id == Prediction.id)
            .filter(
                Prediction.symbol == symbol,
                Signal.status == "CLOSED",
                Outcome.prediction_error.isnot(None),
                Outcome.exit_price.isnot(None),
            )
            .order_by(Outcome.closed_at.desc())
            .limit(window)
            .all()
        )

        if not rows:
            return 0.0, 0.5  # neutral default

        errors = [o.prediction_error for o, _sig, _pred in rows if o.prediction_error is not None]
        if not errors:
            return 0.0, 0.5

        mae = sum(errors) / len(errors)

        # Directional accuracy: did prediction direction match actual price movement?
        correct = 0
        for o, sig, pred in rows:
            if o.exit_price is None or sig.entry_price is None:
                continue
            pred_direction = 1 if pred.predicted_price > sig.entry_price else (-1 if pred.predicted_price < sig.entry_price else 0)
            actual_direction = 1 if o.exit_price > sig.entry_price else (-1 if o.exit_price < sig.entry_price else 0)
            if pred_direction == actual_direction:
                correct += 1

        directional_accuracy = correct / len(rows) if rows else 0.5
        return float(mae), float(directional_accuracy)


def get_symbol_mae(symbol: str) -> float:
    """2026-04-23: Convenience wrapper — returns just the MAE for a symbol (or 0.0 if unknown)."""
    mae, _ = get_prediction_accuracy(symbol, window=20)
    return mae
