from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from v3_pipeline.models.manager import ModelManager
from v3_pipeline.models.strategy_base import StrategyBase


@dataclass
class KiroLSTMStrategy(StrategyBase):
    """KiroLSTM plugin strategy wrapper."""

    model_manager: ModelManager
    name: str = "kiro_lstm"

    def predict(self, frame: pd.DataFrame) -> float:
        return float(self.model_manager.predict(frame))
