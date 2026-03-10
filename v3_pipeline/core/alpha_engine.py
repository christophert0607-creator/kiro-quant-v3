from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

import pandas as pd


REQUIRED_BASE_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume", "data_source"]


def _build_stderr_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


@dataclass
class AlphaConfig:
    wfa_window: int = 240
    top_k_factors: int = 20
    min_factor_coverage: float = 0.85


class KiroAlphaEngine:
    """FinLab-style factor ranking with walk-forward validation (WFA)."""

    def __init__(self, config: AlphaConfig | None = None) -> None:
        self.config = config or AlphaConfig()
        self.logger = _build_stderr_logger(self.__class__.__name__)

    def select_features(self, symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame

        df = frame.copy()
        for col in REQUIRED_BASE_COLUMNS:
            if col not in df.columns:
                df[col] = 0.0 if col != "Date" and col != "data_source" else (pd.Timestamp.utcnow() if col == "Date" else "UNKNOWN")

        y = df["Close"].pct_change().shift(-1)
        candidate_cols = [
            c for c in df.columns
            if c not in REQUIRED_BASE_COLUMNS and c != "Close"
        ]

        if not candidate_cols:
            return df

        w = min(self.config.wfa_window, len(df))
        window_df = df.iloc[-w:].copy()
        window_y = y.iloc[-w:]

        scores: list[tuple[str, float]] = []
        for col in candidate_cols:
            ser = pd.to_numeric(window_df[col], errors="coerce")
            coverage = float(ser.notna().mean())
            if coverage < self.config.min_factor_coverage:
                continue
            ic = ser.corr(window_y)
            if pd.notna(ic):
                scores.append((col, abs(float(ic))))

        scores.sort(key=lambda x: x[1], reverse=True)
        selected_factors = [name for name, _ in scores[: self.config.top_k_factors]]
        if not selected_factors:
            selected_factors = candidate_cols[: self.config.top_k_factors]

        selected_cols = [c for c in REQUIRED_BASE_COLUMNS if c in df.columns] + selected_factors
        selected = df[selected_cols].copy()

        self.logger.info(
            "[WFA][%s] selected_factors=%d/%d window=%d",
            symbol,
            len(selected_factors),
            len(candidate_cols),
            w,
        )
        return selected
