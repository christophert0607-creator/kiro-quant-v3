from __future__ import annotations

import pandas as pd
import torch

from v3_pipeline.models.strategy_base import StrategyBase


class KiroLSTMStrategy(StrategyBase):
    name = "kiro_lstm"

    def predict(self, manager: object, latest_data_window: pd.DataFrame) -> float:
        manager.model.eval()
        with torch.no_grad():
            x = manager.data_preparer.transform_for_inference(latest_data_window).to(manager.device)
            scaled_pred = float(manager.model(x).cpu().numpy().ravel()[0])
            pred = manager.data_preparer.inverse_scale_target(scaled_pred)
            manager.logger.info("[%s] Prediction (scaled=%.6f, inverse=%.6f)", self.name, scaled_pred, pred)
            return pred


class RSIOversoldStrategy(StrategyBase):
    """Reserved plugin strategy scaffold for future RSI oversold logic."""

    name = "rsi_oversold"

    def predict(self, manager: object, latest_data_window: pd.DataFrame) -> float:
        close = float(latest_data_window.iloc[-1]["Close"])
        manager.logger.info("[%s] Placeholder strategy returning close=%.4f", self.name, close)
        return close


class VolumeBreakoutStrategy(StrategyBase):
    """Reserved plugin strategy scaffold for future volume breakout logic."""

    name = "volume_breakout"

    def predict(self, manager: object, latest_data_window: pd.DataFrame) -> float:
        close = float(latest_data_window.iloc[-1]["Close"])
        manager.logger.info("[%s] Placeholder strategy returning close=%.4f", self.name, close)
        return close
