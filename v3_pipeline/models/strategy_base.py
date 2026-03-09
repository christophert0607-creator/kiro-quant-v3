from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StrategyBase(ABC):
    """Pluggable strategy contract for model-driven or rule-driven predictors."""

    name: str = "base"

    @abstractmethod
    def build(self, **kwargs: Any) -> Any:
        """Build and return a runnable strategy/model object."""

    def predict(self, model: Any, features: Any) -> float:
        """Optional common inference hook for non-ModelManager usage."""
        raise NotImplementedError
