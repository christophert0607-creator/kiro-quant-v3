#!/usr/bin/env python3
"""Base strategy interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict
import pandas as pd


class BaseStrategy(ABC):
    name = "base"

    def __init__(self, config: Dict):
        self.config = config or {}

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Dict:
        """Return dict with keys: signal (BUY/SELL/HOLD), price, meta."""
        raise NotImplementedError

