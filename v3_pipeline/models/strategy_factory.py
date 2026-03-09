from __future__ import annotations

from v3_pipeline.models.strategies import KiroLSTMStrategy, RSIOversoldStrategy, VolumeBreakoutStrategy


class StrategyFactory:
    _registry = {
        "kiro_lstm": KiroLSTMStrategy,
        "rsi_oversold": RSIOversoldStrategy,
        "volume_breakout": VolumeBreakoutStrategy,
    }

    @classmethod
    def create(cls, name: str):
        key = (name or "kiro_lstm").strip().lower()
        if key not in cls._registry:
            raise ValueError(f"Unknown strategy '{name}'. Available: {sorted(cls._registry.keys())}")
        return cls._registry[key]()
