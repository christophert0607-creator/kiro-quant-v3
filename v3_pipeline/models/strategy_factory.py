from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from v3_pipeline.models.strategy_base import StrategyBase


@dataclass
class RulePlaceholderStrategy(StrategyBase):
    """Placeholder for future RSI / breakout rule strategies."""

    name: str = "rule_placeholder"

    def predict(self, frame):  # type: ignore[override]
        close = float(frame.iloc[-1]["Close"])
        return close


class StrategyFactory:
    @staticmethod
    def build(name: str, **kwargs: Any) -> StrategyBase:
        strategy = name.lower()
        if strategy == "kiro_lstm":
            from v3_pipeline.models.manager import ModelManager
            from v3_pipeline.models.strategy_plugins import KiroLSTMStrategy

            manager = kwargs.get("model_manager")
            if not isinstance(manager, ModelManager):
                raise ValueError("kiro_lstm strategy requires model_manager=ModelManager")
            return KiroLSTMStrategy(model_manager=manager)

        if strategy in {"rsi_oversold", "volume_breakout"}:
            # Reserved strategy hooks for future rule-based implementations.
            return RulePlaceholderStrategy(name=strategy)

        raise ValueError(f"Unknown strategy '{name}'")
