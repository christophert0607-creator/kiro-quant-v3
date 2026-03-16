#!/usr/bin/env python3
"""
V3.6 — Kelly Formula 資金管理 (根據第六章)
============================================
Kelly Formula: f* = (bp - q) / b
- f*: 最佳投資比例
- b: 淨賠率 (net odds)
- p: 贏的概率
- q: 輸的概率 = 1 - p

功能:
1. Kelly 倉位計算 (支援 半 Kelly / 25%~100% Kelly)
2. 基於歷史績效動態調整
3. 風險規則鐵律執行
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, List

import numpy as np

log = logging.getLogger("V36.Kelly")


@dataclass
class KellyConfig:
    """Kelly 倉位配置"""
    kelly_factor: float = 0.5          # 半 Kelly (0.25 ~ 1.0)
    max_position_per_trade: float = 0.15   # 單筆最大倉位 15%
    max_total_position: float = 0.80       # 最大總倉位 80%
    max_positions: int = 10                # 最大同時持倉數
    risk_per_trade: float = 0.02           # 每筆交易風險 ≤ 2%
    max_daily_risk: float = 0.05           # 單日總風險 ≤ 5%
    min_win_rate: float = 0.40             # 最低勝率門檻 (低於此不交易)
    min_trades_for_kelly: int = 30         # 至少 30 筆交易才用 Kelly


# 倉位計算規則表
KELLY_TIERS = {
    "unverified":   {"kelly_pct": 0.25, "max_position": 0.05},
    "verified":     {"kelly_pct": 0.50, "max_position": 0.10},
    "high_conf":    {"kelly_pct": 0.75, "max_position": 0.15},
    "ultra_conf":   {"kelly_pct": 1.00, "max_position": 0.20},
}


class KellyPositionSizer:
    """
    Kelly Formula 倉位管理器

    Kelly Formula: f* = (bp - q) / b
    """

    def __init__(self, config: Optional[KellyConfig] = None):
        self.config = config or KellyConfig()

    def calculate_kelly(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        計算原始 Kelly 比例

        參數:
        - win_rate: 歷史勝率 (0.0 - 1.0)
        - avg_win: 平均獲利金額
        - avg_loss: 平均虧損金額 (正數)

        返回:
        - Kelly 比例 (0.0 - 1.0)
        """
        if avg_loss <= 0 or win_rate <= 0 or avg_win <= 0:
            return 0.0

        b = avg_win / avg_loss  # 淨賠率
        p = win_rate
        q = 1 - p

        kelly = (b * p - q) / b

        if kelly < 0:
            return 0.0

        return kelly

    def calculate_position_size(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        portfolio_value: float,
        tier: str = "verified",
        current_total_exposure: float = 0.0,
        current_positions_count: int = 0,
        daily_risk_used: float = 0.0,
    ) -> Dict:
        """
        計算建議倉位大小

        參數:
        - win_rate: 歷史勝率
        - avg_win: 平均獲利
        - avg_loss: 平均虧損 (正數)
        - portfolio_value: 組合總值
        - tier: 策略驗證級別 (unverified/verified/high_conf/ultra_conf)
        - current_total_exposure: 當前總倉位比例
        - current_positions_count: 當前持倉數量
        - daily_risk_used: 今日已用風險比例

        返回:
        - dict: {position_value, kelly_raw, kelly_adjusted, pct_of_portfolio, reason}
        """
        result = {
            "position_value": 0.0,
            "kelly_raw": 0.0,
            "kelly_adjusted": 0.0,
            "pct_of_portfolio": 0.0,
            "reason": "",
        }

        # 鐵律 1: 勝率太低不交易
        if win_rate < self.config.min_win_rate:
            result["reason"] = f"win_rate ({win_rate:.2%}) < min ({self.config.min_win_rate:.2%})"
            return result

        # 鐵律 2: 持倉數量上限
        if current_positions_count >= self.config.max_positions:
            result["reason"] = f"max_positions ({current_positions_count}/{self.config.max_positions})"
            return result

        # 鐵律 3: 總倉位上限
        if current_total_exposure >= self.config.max_total_position:
            result["reason"] = f"max_total_position ({current_total_exposure:.2%})"
            return result

        # 鐵律 4: 單日風險上限
        if daily_risk_used >= self.config.max_daily_risk:
            result["reason"] = f"max_daily_risk ({daily_risk_used:.2%})"
            return result

        # 計算原始 Kelly
        kelly_raw = self.calculate_kelly(win_rate, avg_win, avg_loss)
        result["kelly_raw"] = kelly_raw

        if kelly_raw <= 0:
            result["reason"] = "negative_kelly"
            return result

        # 應用 tier 和 factor
        tier_config = KELLY_TIERS.get(tier, KELLY_TIERS["unverified"])
        kelly_adjusted = kelly_raw * tier_config["kelly_pct"] * self.config.kelly_factor

        # 多重上限
        max_limits = [
            tier_config["max_position"],
            self.config.max_position_per_trade,
            self.config.risk_per_trade / (avg_loss / portfolio_value) if avg_loss > 0 and portfolio_value > 0 else 1.0,
            self.config.max_total_position - current_total_exposure,
            self.config.max_daily_risk - daily_risk_used,
        ]

        kelly_capped = min(kelly_adjusted, min(max_limits))
        kelly_capped = max(0.0, kelly_capped)

        position_value = portfolio_value * kelly_capped

        result["kelly_adjusted"] = kelly_capped
        result["pct_of_portfolio"] = kelly_capped
        result["position_value"] = position_value
        result["reason"] = "ok"

        log.info(
            f"Kelly: raw={kelly_raw:.4f} adj={kelly_capped:.4f} "
            f"→ ${position_value:,.2f} ({kelly_capped:.2%} of portfolio)"
        )

        return result

    def calculate_from_trades(
        self,
        trade_results: List[float],
        portfolio_value: float,
        tier: str = "verified",
        **kwargs,
    ) -> Dict:
        """
        從歷史交易結果直接計算倉位

        trade_results: 每筆交易的盈虧列表 (正=盈利, 負=虧損)
        """
        if len(trade_results) < self.config.min_trades_for_kelly:
            return {
                "position_value": 0.0,
                "kelly_raw": 0.0,
                "kelly_adjusted": 0.0,
                "pct_of_portfolio": 0.0,
                "reason": f"insufficient_trades ({len(trade_results)}/{self.config.min_trades_for_kelly})",
            }

        trades = np.array(trade_results)
        wins = trades[trades > 0]
        losses = trades[trades < 0]

        if len(wins) == 0 or len(losses) == 0:
            return {
                "position_value": 0.0,
                "kelly_raw": 0.0,
                "kelly_adjusted": 0.0,
                "pct_of_portfolio": 0.0,
                "reason": "no_wins_or_no_losses",
            }

        win_rate = len(wins) / len(trades)
        avg_win = float(np.mean(wins))
        avg_loss = float(abs(np.mean(losses)))

        return self.calculate_position_size(
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            portfolio_value=portfolio_value,
            tier=tier,
            **kwargs,
        )
