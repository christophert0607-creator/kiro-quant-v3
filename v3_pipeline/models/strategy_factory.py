from __future__ import annotations

from typing import Any

from v3_pipeline.models.brain import KiroLSTM
from v3_pipeline.models.strategy_base import StrategyBase


class KiroLSTMStrategy(StrategyBase):
    name = "kiro_lstm"

    def build(self, **kwargs: Any) -> KiroLSTM:
        return KiroLSTM(**kwargs)


class RSIOversoldStrategy(StrategyBase):
    name = "rsi_oversold"

    def build(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": self.name, "params": kwargs}


class VolumeBreakoutStrategy(StrategyBase):
    name = "volume_breakout"

    def build(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": self.name, "params": kwargs}


class StrategyFactory:
    _registry: dict[str, StrategyBase] = {
        KiroLSTMStrategy.name: KiroLSTMStrategy(),
        RSIOversoldStrategy.name: RSIOversoldStrategy(),
        VolumeBreakoutStrategy.name: VolumeBreakoutStrategy(),
    }

    @classmethod
    def register(cls, strategy: StrategyBase) -> None:
        cls._registry[strategy.name] = strategy

    @classmethod
    def create(cls, strategy_name: str, **kwargs: Any) -> Any:
        key = strategy_name.strip().lower()
        if key not in cls._registry:
            raise ValueError(f"Unknown strategy '{strategy_name}'. Available: {sorted(cls._registry)}")
        return cls._registry[key].build(**kwargs)
