#!/usr/bin/env python3
"""
Kiro-Quant V3.6 — 全新量化交易策略模組
==========================================
根據《量化交易：如何建立自己的演算法交易業務》Ernest Chan 書籍優化

模組:
- KellyPositionSizer: Kelly Formula 資金管理 (第六章)
- TransactionCostCalculator: 交易成本模型 (第三章)
- RiskMetrics: VaR/CVaR/MDD 風險度量 (第六章)
- PerformanceMetrics: 績效評估指標 (第二/三章)
- MarketRegimeDetector: 市場狀態識別 (第七章)
- BacktestEngine: 無偏差回測框架 (第三章)
- V36Config: 完整策略配置 (第七章)
"""

from .kelly_position_sizer import KellyPositionSizer
from .transaction_cost import TransactionCostCalculator
from .risk_metrics import RiskMetrics
from .performance_metrics import PerformanceMetrics
from .market_regime import MarketRegimeDetector
from .backtest_engine import BacktestEngine, BacktestValidator
from .strategy_config import V36Config, load_v36_config

__version__ = "3.6.0"

__all__ = [
    "KellyPositionSizer",
    "TransactionCostCalculator",
    "RiskMetrics",
    "PerformanceMetrics",
    "MarketRegimeDetector",
    "BacktestEngine",
    "BacktestValidator",
    "V36Config",
    "load_v36_config",
]
