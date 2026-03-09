from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class StrategyBase(ABC):
    """Pluggable strategy interface for model-based and rule-based predictors."""

    name: str = "base"

    @abstractmethod
    def predict(self, manager: object, latest_data_window: pd.DataFrame) -> float:
        """Return target price/score prediction for the latest window."""
        raise NotImplementedError
