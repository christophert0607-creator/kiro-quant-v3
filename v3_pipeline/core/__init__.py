"""
Kiro Quant V3 - Core Pipeline Module
Exports all core components for easy importing.
"""

# Alpha Engine
from v3_pipeline.core.alpha_engine import KiroAlphaEngine, AlphaConfig

# Data Connectors
from v3_pipeline.core.futu_connector import FutuConnector
from v3_pipeline.core.history_priming import HistoryPrimer, PrimingConfig

# Trading Loop
from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop

# Monte Carlo Simulator
from v3_pipeline.core.monte_carlo import MonteCarloSimulator

# Strategy Factory
from v3_pipeline.core.strategy_factory import StrategyFactory

# Pipeline version marker
__all__ = [
    # Alpha Engine
    "KiroAlphaEngine",
    "AlphaConfig",
    # Connectors
    "FutuConnector",
    "HistoryPrimer",
    "PrimingConfig",
    # Trading Loop
    "LiveConfig",
    "LiveTradingLoop",
    # Simulation
    "MonteCarloSimulator",
    # Strategy
    "StrategyFactory",
]
