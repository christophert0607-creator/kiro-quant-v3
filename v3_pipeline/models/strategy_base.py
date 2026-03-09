from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class StrategyBase(ABC):
    """Pluggable strategy interface for multi-strategy prediction/execution."""

    name: str = "base"

    @abstractmethod
    def predict(self, frame: pd.DataFrame) -> float:
        """Return next-step target value or signal score."""

    def fit(self, frame: pd.DataFrame) -> None:
        """Optional training hook for strategies that require fitting."""
        return None
