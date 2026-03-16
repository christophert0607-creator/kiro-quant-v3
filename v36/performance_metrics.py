#!/usr/bin/env python3
"""
V3.6 — 績效評估指標 (根據第二/三章)
=======================================
關鍵指標:
1. Sharpe Ratio     — 風險調整收益
2. Information Ratio — 相對基準超額收益
3. Calmar Ratio     — 收益 / 最大回撤
4. Sortino Ratio    — 只考慮下行波動
5. Win Rate         — 勝率
6. Profit Factor    — 盈虧比
7. Max Drawdown     — 最大回撤

上線前必須通過:
1. Sharpe Ratio > 1.0
2. 最大回撤 < 15%
3. 交易成本影響 < 20% 收益
4. 樣本外測試通過
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

log = logging.getLogger("V36.Perf")


@dataclass
class PerformanceThresholds:
    """績效門檻 (上線前必須通過)"""
    min_sharpe: float = 1.0
    max_drawdown: float = 0.15
    max_cost_impact: float = 0.20       # 成本佔收益 < 20%
    min_profit_factor: float = 1.2
    min_win_rate: float = 0.45
    min_trades: int = 50                # 最少交易次數
    risk_free_rate: float = 0.04        # 無風險利率 (年化 4%)


class PerformanceMetrics:
    """
    策略績效評估

    根據 Ernest Chan 書籍建議的關鍵指標
    """

    def __init__(self, thresholds: Optional[PerformanceThresholds] = None):
        self.thresholds = thresholds or PerformanceThresholds()

    def calculate_all_metrics(
        self,
        returns: List[float],
        benchmark_returns: Optional[List[float]] = None,
        risk_free_rate: Optional[float] = None,
    ) -> Dict:
        """
        計算所有績效指標

        參數:
        - returns: 日收益率列表
        - benchmark_returns: 基準收益率 (用於 IR)
        - risk_free_rate: 無風險利率 (年化)

        返回:
        - dict: 所有績效指標
        """
        if not returns or len(returns) < 5:
            return self._empty_metrics("insufficient_data")

        rfr = risk_free_rate if risk_free_rate is not None else self.thresholds.risk_free_rate
        ret = np.array(returns, dtype=float)

        # 過濾 NaN 和 Inf
        ret = ret[np.isfinite(ret)]
        if len(ret) < 5:
            return self._empty_metrics("too_many_nan")

        # ─── 1. 基礎統計 ───
        total_return = float(np.prod(1 + ret) - 1)
        annual_return = float(np.mean(ret) * 252)
        annual_volatility = float(np.std(ret, ddof=1) * np.sqrt(252))

        # ─── 2. Sharpe Ratio ───
        daily_rf = rfr / 252
        excess_returns = ret - daily_rf
        sharpe = float(np.mean(excess_returns) / np.std(excess_returns, ddof=1) * np.sqrt(252)) \
            if np.std(excess_returns, ddof=1) > 0 else 0.0

        # ─── 3. Information Ratio ───
        info_ratio = None
        if benchmark_returns is not None and len(benchmark_returns) == len(ret):
            bench = np.array(benchmark_returns, dtype=float)
            active_returns = ret - bench
            active_std = np.std(active_returns, ddof=1)
            info_ratio = float(np.mean(active_returns) / active_std * np.sqrt(252)) \
                if active_std > 0 else 0.0

        # ─── 4. Maximum Drawdown ───
        equity = np.cumprod(1 + ret)
        running_max = np.maximum.accumulate(equity)
        drawdown = (equity - running_max) / running_max
        max_dd = float(drawdown.min())

        # ─── 5. Calmar Ratio ───
        calmar = float(annual_return / abs(max_dd)) if max_dd != 0 else 0.0

        # ─── 6. Sortino Ratio ───
        downside_returns = ret[ret < 0]
        downside_std = float(np.std(downside_returns, ddof=1)) if len(downside_returns) > 1 else 0.001
        sortino = float(np.mean(excess_returns) / downside_std * np.sqrt(252)) \
            if downside_std > 0 else 0.0

        # ─── 7. Win Rate ───
        win_trades = ret[ret > 0]
        loss_trades = ret[ret < 0]
        win_rate = float(len(win_trades) / len(ret)) if len(ret) > 0 else 0.0

        # ─── 8. Profit Factor ───
        avg_win = float(np.mean(win_trades)) if len(win_trades) > 0 else 0.0
        avg_loss = float(abs(np.mean(loss_trades))) if len(loss_trades) > 0 else 0.001
        profit_factor = float(avg_win / avg_loss) if avg_loss > 0 else 0.0

        # ─── 9. 其他指標 ───
        total_trades = len(ret)
        max_consecutive_wins = self._max_consecutive(ret > 0)
        max_consecutive_losses = self._max_consecutive(ret < 0)

        metrics = {
            "total_return": round(total_return, 6),
            "annual_return": round(annual_return, 6),
            "annual_volatility": round(annual_volatility, 6),
            "sharpe_ratio": round(sharpe, 4),
            "information_ratio": round(info_ratio, 4) if info_ratio is not None else None,
            "calmar_ratio": round(calmar, 4),
            "sortino_ratio": round(sortino, 4),
            "max_drawdown": round(max_dd, 6),
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 6),
            "avg_loss": round(avg_loss, 6),
            "profit_factor": round(profit_factor, 4),
            "total_trades": total_trades,
            "max_consecutive_wins": max_consecutive_wins,
            "max_consecutive_losses": max_consecutive_losses,
        }

        return metrics

    def validate_for_launch(
        self,
        metrics: Dict,
        cost_impact: float = 0.0,
    ) -> Dict:
        """
        上線前驗證

        檢查所有績效是否達到門檻

        返回:
        - dict: {passed, checks, failed_checks}
        """
        checks = {}

        # 1. Sharpe Ratio
        sharpe = metrics.get("sharpe_ratio", 0)
        checks["sharpe"] = {
            "value": sharpe,
            "threshold": self.thresholds.min_sharpe,
            "passed": sharpe >= self.thresholds.min_sharpe,
        }

        # 2. Max Drawdown
        max_dd = abs(metrics.get("max_drawdown", 0))
        checks["max_drawdown"] = {
            "value": max_dd,
            "threshold": self.thresholds.max_drawdown,
            "passed": max_dd <= self.thresholds.max_drawdown,
        }

        # 3. 成本影響
        checks["cost_impact"] = {
            "value": cost_impact,
            "threshold": self.thresholds.max_cost_impact,
            "passed": cost_impact <= self.thresholds.max_cost_impact,
        }

        # 4. Profit Factor
        pf = metrics.get("profit_factor", 0)
        checks["profit_factor"] = {
            "value": pf,
            "threshold": self.thresholds.min_profit_factor,
            "passed": pf >= self.thresholds.min_profit_factor,
        }

        # 5. Win Rate
        wr = metrics.get("win_rate", 0)
        checks["win_rate"] = {
            "value": wr,
            "threshold": self.thresholds.min_win_rate,
            "passed": wr >= self.thresholds.min_win_rate,
        }

        # 6. 交易次數
        total = metrics.get("total_trades", 0)
        checks["total_trades"] = {
            "value": total,
            "threshold": self.thresholds.min_trades,
            "passed": total >= self.thresholds.min_trades,
        }

        failed = [k for k, v in checks.items() if not v["passed"]]
        passed = len(failed) == 0

        result = {
            "passed": passed,
            "checks": checks,
            "failed_checks": failed,
        }

        if passed:
            log.info("✅ 所有績效指標通過上線門檻")
        else:
            log.warning(f"⚠️ {len(failed)} 項未通過: {failed}")

        return result

    def format_report(self, metrics: Dict) -> str:
        """格式化績效報告字符串"""
        lines = [
            "=" * 50,
            "📊 V3.6 策略績效報告",
            "=" * 50,
            f"  總收益:         {metrics.get('total_return', 0):.2%}",
            f"  年化收益:       {metrics.get('annual_return', 0):.2%}",
            f"  年化波動:       {metrics.get('annual_volatility', 0):.2%}",
            f"  Sharpe Ratio:   {metrics.get('sharpe_ratio', 0):.4f}",
            f"  Sortino Ratio:  {metrics.get('sortino_ratio', 0):.4f}",
            f"  Calmar Ratio:   {metrics.get('calmar_ratio', 0):.4f}",
            f"  最大回撤:       {metrics.get('max_drawdown', 0):.2%}",
            f"  勝率:           {metrics.get('win_rate', 0):.2%}",
            f"  盈虧比:         {metrics.get('profit_factor', 0):.4f}",
            f"  總交易次數:     {metrics.get('total_trades', 0)}",
            f"  最大連勝:       {metrics.get('max_consecutive_wins', 0)}",
            f"  最大連敗:       {metrics.get('max_consecutive_losses', 0)}",
        ]
        if metrics.get("information_ratio") is not None:
            lines.append(f"  Information Ratio: {metrics['information_ratio']:.4f}")
        lines.append("=" * 50)
        return "\n".join(lines)

    @staticmethod
    def _max_consecutive(mask: np.ndarray) -> int:
        """計算最大連續 True 次數"""
        if len(mask) == 0:
            return 0
        max_count = 0
        count = 0
        for b in mask:
            if b:
                count += 1
                max_count = max(max_count, count)
            else:
                count = 0
        return max_count

    @staticmethod
    def _empty_metrics(reason: str) -> Dict:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "annual_volatility": 0.0,
            "sharpe_ratio": 0.0,
            "calmar_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_trades": 0,
            "reason": reason,
        }
