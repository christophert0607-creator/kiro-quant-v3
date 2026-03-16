#!/usr/bin/env python3
"""
V3.6 — 完整策略配置 (第七部分)
===================================
集中管理所有 V3.6 模組配置

配置包含:
- money_management: Kelly 資金管理
- transaction_costs: 交易成本
- risk_management: 風險控制
- strategies: 策略參數
- backtest: 回測設定
- symbols: 股票池
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

log = logging.getLogger("V36.Config")


@dataclass
class V36Config:
    """V3.6 完整策略配置"""

    strategy_version: str = "3.6"

    # ─── 資金管理 ───
    kelly_factor: float = 0.5
    max_position_per_trade: float = 0.15
    max_total_position: float = 0.80
    max_positions: int = 10
    risk_per_trade: float = 0.02
    max_daily_risk: float = 0.05

    # ─── 交易成本 ───
    commission_rate: float = 0.001
    slippage_large_cap: float = 0.0005
    slippage_mid_cap: float = 0.001
    slippage_small_cap: float = 0.002
    min_cost_threshold: float = 0.001

    # ─── 風險管理 ───
    var_confidence: float = 0.95
    var_stop_loss: bool = True
    max_drawdown_stop: float = 0.15
    max_drawdown_pause: float = 0.20
    max_drawdown_kill: float = 0.30

    # ─── 均值回歸策略 ───
    mean_reversion_enabled: bool = True
    rsi_oversold: int = 30
    rsi_overbought: int = 70
    rsi_lookback: int = 14

    # ─── 動量策略 ───
    momentum_enabled: bool = True
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # ─── 突破策略 ───
    breakout_enabled: bool = False
    breakout_lookback: int = 20
    breakout_volume_threshold: float = 1.5

    # ─── 回測 ───
    train_test_split: float = 0.7
    walk_forward: bool = True
    cost_included: bool = True

    # ─── 股票池 ───
    symbols_us: List[str] = field(
        default_factory=lambda: ["NVDA", "TSLA", "AMD", "META", "GOOGL"]
    )
    symbols_hk: List[str] = field(
        default_factory=lambda: ["0700.HK", "9988.HK", "3690.HK"]
    )

    def to_dict(self) -> Dict:
        """轉換為字典"""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """轉換為 JSON 字符串"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, filepath: str) -> None:
        """保存配置到文件"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.to_json())
        log.info(f"V3.6 config saved to {filepath}")

    @classmethod
    def from_dict(cls, data: Dict) -> "V36Config":
        """從字典創建配置"""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def from_json(cls, filepath: str) -> "V36Config":
        """從 JSON 文件載入配置"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 支援嵌套格式 (V3.6_STRATEGY_DESIGN.md 中的配置格式)
        flat = {}
        if "money_management" in data:
            flat.update(data["money_management"])
        if "transaction_costs" in data:
            flat.update(data["transaction_costs"])
        if "risk_management" in data:
            flat.update(data["risk_management"])
        if "strategies" in data:
            for strat_name, strat_cfg in data["strategies"].items():
                if strat_name == "mean_reversion":
                    flat["mean_reversion_enabled"] = strat_cfg.get("enabled", True)
                    flat["rsi_oversold"] = strat_cfg.get("rsi_oversold", 30)
                    flat["rsi_overbought"] = strat_cfg.get("rsi_overbought", 70)
                    flat["rsi_lookback"] = strat_cfg.get("lookback_period", 14)
                elif strat_name == "momentum":
                    flat["momentum_enabled"] = strat_cfg.get("enabled", True)
                    flat["macd_fast"] = strat_cfg.get("macd_fast", 12)
                    flat["macd_slow"] = strat_cfg.get("macd_slow", 26)
                    flat["macd_signal"] = strat_cfg.get("macd_signal", 9)
                elif strat_name == "breakout":
                    flat["breakout_enabled"] = strat_cfg.get("enabled", False)
                    flat["breakout_lookback"] = strat_cfg.get("lookback_period", 20)
                    flat["breakout_volume_threshold"] = strat_cfg.get("volume_threshold", 1.5)
        if "backtest" in data:
            flat.update(data["backtest"])
        if "symbols" in data:
            if "US" in data["symbols"]:
                flat["symbols_us"] = data["symbols"]["US"]
            if "HK" in data["symbols"]:
                flat["symbols_hk"] = data["symbols"]["HK"]

        # 如果是扁平格式，直接使用
        if not flat:
            flat = data

        return cls.from_dict(flat)


def load_v36_config(config_path: Optional[str] = None) -> V36Config:
    """
    載入 V3.6 配置

    優先順序:
    1. 傳入的 config_path
    2. 環境變數 V36_CONFIG_PATH
    3. 同目錄下的 v36_config.json
    4. 默認配置
    """
    paths_to_try = []

    if config_path:
        paths_to_try.append(config_path)

    env_path = os.environ.get("V36_CONFIG_PATH")
    if env_path:
        paths_to_try.append(env_path)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths_to_try.append(os.path.join(base_dir, "v36_config.json"))

    for path in paths_to_try:
        if os.path.exists(path):
            log.info(f"Loading V3.6 config from {path}")
            return V36Config.from_json(path)

    log.info("Using default V3.6 config")
    return V36Config()
