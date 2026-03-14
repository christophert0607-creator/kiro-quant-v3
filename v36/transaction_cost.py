#!/usr/bin/env python3
"""
V3.6 — 交易成本模型 (根據第三章)
===================================
根據書籍建議：真實交易成本必須計入回測

成本組成:
1. 佣金 (Commission)
2. 滑點 (Slippage)
3. 印花稅 (Stamp Duty, HK only)

成本門檻檢查:
- 預期收益 < 成本比率 → 不執行
- 回測必須使用成本後淨收益
- 建議成本緩衝: +20%
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

log = logging.getLogger("V36.Cost")


@dataclass
class CostConfig:
    """交易成本配置"""
    # 佣金率
    us_stock_commission: float = 0.001     # 美股 0.1%
    hk_stock_commission: float = 0.001     # 港股 0.1%
    futures_commission: float = 0.0002     # 期貨 0.02%

    # HK 印花稅
    hk_stamp_duty: float = 0.001           # 0.1%

    # 滑點模型
    slippage_large_cap: float = 0.0005     # 大型股 0.05%
    slippage_mid_cap: float = 0.001        # 中型股 0.1%
    slippage_small_cap: float = 0.002      # 小型股 0.2%

    # 最低成本門檻 (如策略預期收益低於此比率 → 不交易)
    min_cost_threshold: float = 0.001      # 0.1%

    # 成本緩衝 (預防市場條件變化)
    cost_buffer_pct: float = 0.20          # +20%

    # 最低佣金 (USD)
    min_commission_usd: float = 1.0


class TransactionCostCalculator:
    """
    交易成本計算器

    根據 Ernest Chan 書籍 第三章:
    Real trading costs MUST be included in backtesting.
    """

    def __init__(self, config: Optional[CostConfig] = None):
        self.config = config or CostConfig()

    def calculate_total_cost(
        self,
        symbol: str,
        quantity: int,
        price: float,
        market_cap: str = "large_cap",
        side: str = "BUY",
    ) -> Dict:
        """
        計算總交易成本

        參數:
        - symbol: 股票代碼
        - quantity: 數量
        - price: 價格
        - market_cap: 市值類型 (large_cap / mid_cap / small_cap)
        - side: BUY / SELL

        返回:
        - dict: 成本明細
        """
        if quantity <= 0 or price <= 0:
            return {
                "commission": 0.0,
                "slippage": 0.0,
                "stamp_duty": 0.0,
                "total_cost": 0.0,
                "cost_rate": 0.0,
                "effective_price": price,
                "cost_with_buffer": 0.0,
            }

        trade_value = quantity * price

        # 1. 佣金
        if self._is_hk_stock(symbol):
            commission = max(
                trade_value * self.config.hk_stock_commission,
                self.config.min_commission_usd
            )
            stamp_duty = trade_value * self.config.hk_stamp_duty
        elif self._is_futures(symbol):
            commission = trade_value * self.config.futures_commission
            stamp_duty = 0.0
        else:
            # 默認美股
            commission = max(
                trade_value * self.config.us_stock_commission,
                self.config.min_commission_usd
            )
            stamp_duty = 0.0

        # 2. 滑點
        slippage_rate = {
            "large_cap": self.config.slippage_large_cap,
            "mid_cap": self.config.slippage_mid_cap,
            "small_cap": self.config.slippage_small_cap,
        }.get(market_cap, self.config.slippage_mid_cap)

        slippage = trade_value * slippage_rate

        # 3. 總成本
        total_cost = commission + slippage + stamp_duty
        cost_rate = total_cost / trade_value if trade_value > 0 else 0.0
        cost_with_buffer = total_cost * (1 + self.config.cost_buffer_pct)

        # 4. 有效價格
        if side == "BUY":
            effective_price = price * (1 + cost_rate)
        else:
            effective_price = price * (1 - cost_rate)

        result = {
            "commission": round(commission, 4),
            "slippage": round(slippage, 4),
            "stamp_duty": round(stamp_duty, 4),
            "total_cost": round(total_cost, 4),
            "cost_rate": round(cost_rate, 6),
            "effective_price": round(effective_price, 4),
            "cost_with_buffer": round(cost_with_buffer, 4),
            "trade_value": round(trade_value, 2),
        }

        log.debug(
            f"Cost [{symbol}] {side} {quantity}@${price:.2f}: "
            f"total=${total_cost:.4f} ({cost_rate:.4%})"
        )

        return result

    def is_profitable_after_cost(
        self,
        expected_return: float,
        symbol: str,
        quantity: int,
        price: float,
        market_cap: str = "large_cap",
    ) -> Dict:
        """
        檢查策略在交易成本後是否仍然有利可圖

        參數:
        - expected_return: 預期收益率
        - symbol, quantity, price, market_cap: 交易參數

        返回:
        - dict: {profitable, expected_net_return, round_trip_cost_rate}
        """
        buy_cost = self.calculate_total_cost(symbol, quantity, price, market_cap, "BUY")
        sell_cost = self.calculate_total_cost(symbol, quantity, price, market_cap, "SELL")

        # 來回成本
        round_trip_cost_rate = buy_cost["cost_rate"] + sell_cost["cost_rate"]
        round_trip_cost_with_buffer = round_trip_cost_rate * (1 + self.config.cost_buffer_pct)

        net_return = expected_return - round_trip_cost_with_buffer
        profitable = net_return > self.config.min_cost_threshold

        result = {
            "profitable": profitable,
            "expected_return": expected_return,
            "round_trip_cost_rate": round(round_trip_cost_rate, 6),
            "round_trip_cost_with_buffer": round(round_trip_cost_with_buffer, 6),
            "expected_net_return": round(net_return, 6),
        }

        if not profitable:
            log.warning(
                f"⚠️ [{symbol}] 預期收益 ({expected_return:.4%}) < "
                f"成本+緩衝 ({round_trip_cost_with_buffer:.4%}) → 不建議交易"
            )

        return result

    def apply_cost_to_returns(
        self,
        gross_returns: list,
        symbol: str,
        quantity: int = 100,
        price: float = 100.0,
        market_cap: str = "large_cap",
    ) -> list:
        """
        將交易成本應用到回測收益序列

        gross_returns: 毛收益列表
        返回: 淨收益列表
        """
        cost = self.calculate_total_cost(symbol, quantity, price, market_cap)
        cost_per_trade = cost["cost_rate"]

        net_returns = []
        for r in gross_returns:
            if r != 0:
                # 每次交易扣除成本
                net_r = r - cost_per_trade
            else:
                net_r = r
            net_returns.append(net_r)

        return net_returns

    @staticmethod
    def _is_hk_stock(symbol: str) -> bool:
        return symbol.endswith(".HK") or symbol.startswith("HK.")

    @staticmethod
    def _is_futures(symbol: str) -> bool:
        futures_suffixes = (".FUT", ".F", "/ES", "/NQ", "/CL")
        return any(symbol.endswith(s) for s in futures_suffixes)
