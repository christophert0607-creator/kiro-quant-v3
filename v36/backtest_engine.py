#!/usr/bin/env python3
"""
V3.6 — 無偏差回測框架 (根據第三章)
=======================================
回測陷阱防範:
1. 前瞻偏差 (Look-ahead Bias) 檢查
2. 生存者偏差 (Survivorship Bias) 檢查
3. 數據窺探偏差 (Data Snooping) — 樣本外測試
4. Walk-forward Analysis

回測流程:
1. 數據切分 (Train 70% / Test 30%)
2. 信號生成 (防止前瞻偏差)
3. 交易成本扣除
4. 績效評估
5. 樣本外驗證
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Callable, Tuple

import numpy as np

from .transaction_cost import TransactionCostCalculator, CostConfig
from .performance_metrics import PerformanceMetrics, PerformanceThresholds
from .risk_metrics import RiskMetrics, RiskControlConfig

log = logging.getLogger("V36.Backtest")


@dataclass
class BacktestConfig:
    """回測配置"""
    train_test_split: float = 0.7        # 訓練/測試比例
    walk_forward: bool = True            # Walk-forward 分析
    walk_forward_windows: int = 5        # Walk-forward 窗口數
    cost_included: bool = True           # 包含交易成本
    initial_capital: float = 100000.0    # 初始資金
    default_quantity: int = 100          # 默認下單量
    default_market_cap: str = "large_cap"


class BacktestValidator:
    """
    回測驗證器

    防止書籍提到的各種回測偏差
    """

    @staticmethod
    def check_look_ahead_bias(
        signals: List[Dict],
        prices: List[Dict],
    ) -> Dict:
        """
        前瞻偏差檢查

        確保信號不會使用未來數據
        信號時間必須 <= 數據時間
        """
        violations = []
        for i in range(len(signals) - 1):
            sig_time = signals[i].get("timestamp", 0)
            price_time = prices[i].get("timestamp", 0) if i < len(prices) else 0

            if sig_time > price_time and price_time > 0:
                violations.append({
                    "index": i,
                    "signal_time": sig_time,
                    "price_time": price_time,
                })

        passed = len(violations) == 0
        if not passed:
            log.warning(f"⚠️ 前瞻偏差: {len(violations)} 個違規")

        return {
            "check": "look_ahead_bias",
            "passed": passed,
            "violations": violations[:10],  # 最多顯示 10 個
            "total_violations": len(violations),
        }

    @staticmethod
    def check_survivorship_bias(
        symbols_in_data: List[str],
        current_listed: List[str],
        delisted: Optional[List[str]] = None,
    ) -> Dict:
        """
        生存者偏差檢查

        確保包含已退市/破產的股票
        """
        if delisted is None:
            delisted = []

        # 檢查是否有退市股票在數據中
        has_delisted = any(s in symbols_in_data for s in delisted)

        # 如果所有股票都還在上市 → 可能有生存者偏差
        suspicious = all(s in current_listed for s in symbols_in_data)

        return {
            "check": "survivorship_bias",
            "passed": not suspicious or has_delisted,
            "warning": "All symbols are currently listed — may have survivorship bias" if suspicious else None,
            "total_symbols": len(symbols_in_data),
            "has_delisted": has_delisted,
        }

    @staticmethod
    def out_of_sample_test(
        data: List,
        train_ratio: float = 0.7,
    ) -> Tuple[List, List]:
        """
        樣本外測試 — 數據切分

        防止數據窺探偏差
        """
        split_point = int(len(data) * train_ratio)
        train_data = data[:split_point]
        test_data = data[split_point:]

        log.info(f"Data split: Train={len(train_data)}, Test={len(test_data)} ({train_ratio:.0%}/{1-train_ratio:.0%})")

        return train_data, test_data


class BacktestEngine:
    """
    完整回測引擎

    整合成本模型 + 績效評估 + 風險度量
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()
        self.cost_calculator = TransactionCostCalculator()
        self.perf_metrics = PerformanceMetrics()
        self.risk_metrics = RiskMetrics()
        self.validator = BacktestValidator()

    def run_backtest(
        self,
        prices: List[float],
        signals: List[str],
        symbol: str = "US.TSLA",
        initial_capital: Optional[float] = None,
    ) -> Dict:
        """
        執行回測

        參數:
        - prices: 價格序列
        - signals: 信號序列 (BUY / SELL / HOLD)
        - symbol: 股票代碼
        - initial_capital: 初始資金

        返回:
        - dict: 完整回測結果
        """
        capital = initial_capital or self.config.initial_capital

        if len(prices) != len(signals):
            return {"error": "prices and signals must have same length"}

        # ─── 回測主循環 ───
        equity_curve = [capital]
        returns = []
        trades = []
        position = 0  # 0=空倉, >0=持倉
        entry_price = 0.0
        entry_idx = 0
        total_cost = 0.0

        for i in range(len(prices)):
            signal = signals[i]
            price = prices[i]

            if signal == "BUY" and position == 0:
                # 開倉
                qty = int(capital * 0.1 / price) if price > 0 else 0
                if qty <= 0:
                    returns.append(0.0)
                    equity_curve.append(equity_curve[-1])
                    continue

                # 計算成本
                if self.config.cost_included:
                    cost = self.cost_calculator.calculate_total_cost(
                        symbol, qty, price, self.config.default_market_cap, "BUY"
                    )
                    total_cost += cost["total_cost"]

                position = qty
                entry_price = price
                entry_idx = i
                returns.append(0.0)
                equity_curve.append(equity_curve[-1])

            elif signal == "SELL" and position > 0:
                # 平倉
                pnl = (price - entry_price) * position

                # 扣除成本
                if self.config.cost_included:
                    cost = self.cost_calculator.calculate_total_cost(
                        symbol, position, price, self.config.default_market_cap, "SELL"
                    )
                    pnl -= cost["total_cost"]
                    total_cost += cost["total_cost"]

                trade_return = pnl / (entry_price * position) if entry_price > 0 else 0
                returns.append(trade_return)

                trades.append({
                    "entry_idx": entry_idx,
                    "exit_idx": i,
                    "entry_price": entry_price,
                    "exit_price": price,
                    "quantity": position,
                    "pnl": round(pnl, 2),
                    "return": round(trade_return, 6),
                    "holding_period": i - entry_idx,
                })

                capital += pnl
                equity_curve.append(capital)
                position = 0
                entry_price = 0.0

            else:
                # HOLD 或無法執行
                if position > 0:
                    unrealized = (price - entry_price) / entry_price if entry_price > 0 else 0
                    returns.append(unrealized)
                    equity_curve.append(capital + (price - entry_price) * position)
                else:
                    returns.append(0.0)
                    equity_curve.append(equity_curve[-1])

        # ─── 績效計算 ───
        trade_returns = [t["return"] for t in trades] if trades else returns
        metrics = self.perf_metrics.calculate_all_metrics(trade_returns)

        # ─── 風險計算 ───
        risk_report = self.risk_metrics.full_risk_report(
            returns, equity_curve, capital
        )

        # ─── 成本影響 ───
        gross_pnl = sum(t["pnl"] for t in trades) if trades else 0
        cost_impact = total_cost / abs(gross_pnl) if gross_pnl != 0 else 0

        result = {
            "initial_capital": self.config.initial_capital,
            "final_capital": round(capital, 2),
            "total_pnl": round(capital - self.config.initial_capital, 2),
            "total_cost": round(total_cost, 2),
            "cost_impact": round(cost_impact, 4),
            "total_trades": len(trades),
            "trades": trades,
            "metrics": metrics,
            "risk": risk_report,
            "equity_curve": equity_curve,
        }

        log.info(
            f"Backtest Complete: PnL=${result['total_pnl']:.2f} "
            f"Trades={len(trades)} Sharpe={metrics.get('sharpe_ratio', 0):.4f}"
        )

        return result

    def walk_forward_analysis(
        self,
        prices: List[float],
        signal_generator: Callable,
        symbol: str = "US.TSLA",
    ) -> Dict:
        """
        Walk-Forward Analysis

        將數據分成多個窗口，在每個窗口上:
        1. 用 train 數據訓練
        2. 用 test 數據測試

        參數:
        - prices: 完整價格序列
        - signal_generator: 信號生成函數 (prices -> signals)
        - symbol: 股票代碼

        返回:
        - dict: 各窗口結果 + 整體結果
        """
        n_windows = self.config.walk_forward_windows
        window_size = len(prices) // n_windows

        if window_size < 50:
            return {"error": "insufficient data for walk-forward analysis"}

        window_results = []
        all_test_returns = []

        for w in range(n_windows):
            start = w * window_size
            end = min(start + window_size, len(prices))
            window_prices = prices[start:end]

            # 切分 train/test
            split = int(len(window_prices) * self.config.train_test_split)
            train_prices = window_prices[:split]
            test_prices = window_prices[split:]

            # 生成信號 (只用 train 數據訓練)
            test_signals = signal_generator(train_prices, test_prices)

            # 回測 test 部分
            bt_result = self.run_backtest(test_prices, test_signals, symbol)

            # 蒐集結果
            trade_returns = [t["return"] for t in bt_result.get("trades", [])]
            all_test_returns.extend(trade_returns)

            window_results.append({
                "window": w + 1,
                "train_size": len(train_prices),
                "test_size": len(test_prices),
                "pnl": bt_result.get("total_pnl", 0),
                "trades": bt_result.get("total_trades", 0),
                "sharpe": bt_result.get("metrics", {}).get("sharpe_ratio", 0),
            })

        # 整體 OOS 績效
        overall_metrics = self.perf_metrics.calculate_all_metrics(all_test_returns)

        return {
            "windows": window_results,
            "overall_oos_metrics": overall_metrics,
            "total_oos_trades": len(all_test_returns),
        }

    def validate_strategy(
        self,
        backtest_result: Dict,
    ) -> Dict:
        """
        策略驗證 — 上線前檢查

        返回: 驗證結果
        """
        metrics = backtest_result.get("metrics", {})
        cost_impact = backtest_result.get("cost_impact", 0)

        validation = self.perf_metrics.validate_for_launch(metrics, cost_impact)

        log.info(
            f"Strategy Validation: {'✅ PASSED' if validation['passed'] else '❌ FAILED'} "
            f"(failed: {validation.get('failed_checks', [])})"
        )

        return validation
