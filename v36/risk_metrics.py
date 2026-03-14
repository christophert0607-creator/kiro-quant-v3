#!/usr/bin/env python3
"""
V3.6 — 進階風險度量系統 (根據第六章)
=======================================
VaR (Value at Risk):
  在 confidence_level 下的最大損失
  例: VaR 95% = $1000 → 95% 概率下最大損失不超過 $1000

CVaR (Conditional VaR / Expected Shortfall):
  超過 VaR 的平均損失
  即: 當損失超過 VaR 時，平均損失是多少

最大回撤 (Maximum Drawdown):
  從峰值到谷底的最大跌幅

風險控制規則:
- VaR 報警: 當日損失 > VaR 95% → 暫停交易
- CVaR 報警: 損失 > CVaR 95% → 全面平倉
- 最大回撤 > 15% → 停止新交易
- 最大回撤 > 20% → 防守模式 (只平倉不開倉)
- 最大回撤 > 30% → 策略暫停
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("V36.Risk")


@dataclass
class RiskControlConfig:
    """風險控制配置"""
    var_confidence: float = 0.95
    var_stop_loss: bool = True          # VaR 觸發暫停
    cvar_emergency_stop: bool = True    # CVaR 觸發全面平倉

    # 最大回撤閾值
    max_drawdown_stop: float = 0.15     # > 15% → 停止新交易
    max_drawdown_pause: float = 0.20    # > 20% → 防守模式
    max_drawdown_kill: float = 0.30     # > 30% → 策略暫停

    # VaR 計算方法
    var_method: str = "historical"      # "historical" / "parametric" / "monte_carlo"
    var_lookback_days: int = 252         # 1 年


class RiskMetrics:
    """
    進階風險度量

    根據 Ernest Chan 書籍 第六章:
    完整的風險度量系統 (VaR / CVaR / MDD)
    """

    def __init__(self, config: Optional[RiskControlConfig] = None):
        self.config = config or RiskControlConfig()

    def calculate_var(
        self,
        returns: List[float],
        portfolio_value: float,
        confidence_level: Optional[float] = None,
    ) -> Dict:
        """
        VaR (Value at Risk) — 歷史模擬法

        在 confidence_level 下的最大損失

        返回:
        - dict: {var_return, var_dollar, confidence, method}
        """
        if not returns or len(returns) < 10:
            return {"var_return": 0.0, "var_dollar": 0.0, "confidence": 0.0, "method": "insufficient_data"}

        confidence = confidence_level or self.config.var_confidence
        returns_arr = np.array(returns, dtype=float)

        if self.config.var_method == "parametric":
            var_return = self._parametric_var(returns_arr, confidence)
        else:
            # 歷史模擬法
            var_percentile = (1 - confidence) * 100
            var_return = float(np.percentile(returns_arr, var_percentile))

        var_dollar = portfolio_value * abs(var_return)

        return {
            "var_return": round(var_return, 6),
            "var_dollar": round(var_dollar, 2),
            "confidence": confidence,
            "method": self.config.var_method,
        }

    def calculate_cvar(
        self,
        returns: List[float],
        portfolio_value: float,
        confidence_level: Optional[float] = None,
    ) -> Dict:
        """
        CVaR (Conditional VaR / Expected Shortfall)

        超過 VaR 的平均損失
        """
        if not returns or len(returns) < 10:
            return {"cvar_return": 0.0, "cvar_dollar": 0.0}

        confidence = confidence_level or self.config.var_confidence
        returns_arr = np.array(returns, dtype=float)

        var_percentile = (1 - confidence) * 100
        var_return = float(np.percentile(returns_arr, var_percentile))

        # 超過 VaR 的所有損失的平均值
        tail_losses = returns_arr[returns_arr <= var_return]
        if len(tail_losses) == 0:
            cvar_return = var_return
        else:
            cvar_return = float(np.mean(tail_losses))

        cvar_dollar = portfolio_value * abs(cvar_return)

        return {
            "cvar_return": round(cvar_return, 6),
            "cvar_dollar": round(cvar_dollar, 2),
            "confidence": confidence,
        }

    def calculate_max_drawdown(self, equity_curve: List[float]) -> Dict:
        """
        最大回撤 (Maximum Drawdown)

        從峰值到谷底的最大跌幅

        返回:
        - dict: {max_drawdown, peak_idx, trough_idx, peak_value, trough_value, recovery_idx}
        """
        if not equity_curve or len(equity_curve) < 2:
            return {"max_drawdown": 0.0}

        equity = np.array(equity_curve, dtype=float)
        running_max = np.maximum.accumulate(equity)
        drawdown = (equity - running_max) / running_max

        max_dd = float(drawdown.min())
        trough_idx = int(np.argmin(drawdown))
        peak_idx = int(np.argmax(equity[:trough_idx + 1])) if trough_idx > 0 else 0

        # 找恢復點
        recovery_idx = None
        peak_value = float(equity[peak_idx])
        for i in range(trough_idx + 1, len(equity)):
            if equity[i] >= peak_value:
                recovery_idx = i
                break

        return {
            "max_drawdown": round(max_dd, 6),
            "peak_idx": peak_idx,
            "trough_idx": trough_idx,
            "peak_value": round(peak_value, 2),
            "trough_value": round(float(equity[trough_idx]), 2),
            "recovery_idx": recovery_idx,
            "drawdown_series": drawdown.tolist(),
        }

    def calculate_rolling_var(
        self,
        returns: List[float],
        portfolio_value: float,
        window: int = 60,
    ) -> List[Dict]:
        """
        滾動 VaR 計算

        每 window 期計算一次 VaR
        """
        if len(returns) < window:
            return []

        results = []
        returns_arr = np.array(returns, dtype=float)

        for i in range(window, len(returns_arr)):
            window_returns = returns_arr[i - window:i]
            var_result = self.calculate_var(window_returns.tolist(), portfolio_value)
            var_result["index"] = i
            results.append(var_result)

        return results

    def full_risk_report(
        self,
        returns: List[float],
        equity_curve: List[float],
        portfolio_value: float,
    ) -> Dict:
        """
        完整風險報告

        返回:
        - dict: VaR + CVaR + MDD + 風險等級 + 建議動作
        """
        var_result = self.calculate_var(returns, portfolio_value)
        cvar_result = self.calculate_cvar(returns, portfolio_value)
        mdd_result = self.calculate_max_drawdown(equity_curve)

        max_dd = abs(mdd_result.get("max_drawdown", 0))

        # 風險等級判定
        risk_level, action = self._determine_risk_level(max_dd)

        report = {
            "var": var_result,
            "cvar": cvar_result,
            "max_drawdown": mdd_result,
            "risk_level": risk_level,
            "action": action,
            "portfolio_value": portfolio_value,
        }

        log.info(
            f"Risk Report: VaR={var_result['var_dollar']:.2f} "
            f"CVaR={cvar_result['cvar_dollar']:.2f} "
            f"MDD={max_dd:.2%} → {risk_level} ({action})"
        )

        return report

    def check_daily_risk(
        self,
        daily_pnl: float,
        returns_history: List[float],
        portfolio_value: float,
    ) -> Dict:
        """
        每日風險檢查

        返回:
        - dict: {var_breached, cvar_breached, action}
        """
        var = self.calculate_var(returns_history, portfolio_value)
        cvar = self.calculate_cvar(returns_history, portfolio_value)

        daily_loss = abs(daily_pnl) if daily_pnl < 0 else 0

        var_breached = daily_loss > var["var_dollar"] if var["var_dollar"] > 0 else False
        cvar_breached = daily_loss > cvar["cvar_dollar"] if cvar["cvar_dollar"] > 0 else False

        if cvar_breached and self.config.cvar_emergency_stop:
            action = "EMERGENCY_LIQUIDATE"
            log.critical(f"🚨 CVaR 突破! 損失 ${daily_loss:.2f} > CVaR ${cvar['cvar_dollar']:.2f}")
        elif var_breached and self.config.var_stop_loss:
            action = "PAUSE_TRADING"
            log.warning(f"⚠️ VaR 突破! 損失 ${daily_loss:.2f} > VaR ${var['var_dollar']:.2f}")
        else:
            action = "CONTINUE"

        return {
            "daily_pnl": daily_pnl,
            "daily_loss": daily_loss,
            "var": var,
            "cvar": cvar,
            "var_breached": var_breached,
            "cvar_breached": cvar_breached,
            "action": action,
        }

    def _determine_risk_level(self, max_dd: float) -> Tuple[str, str]:
        """根據最大回撤判定風險等級"""
        if max_dd >= self.config.max_drawdown_kill:
            return "CRITICAL", "STRATEGY_PAUSE"
        elif max_dd >= self.config.max_drawdown_pause:
            return "HIGH", "DEFENSE_MODE"
        elif max_dd >= self.config.max_drawdown_stop:
            return "ELEVATED", "STOP_NEW_TRADES"
        elif max_dd >= 0.10:
            return "MODERATE", "REDUCE_POSITION"
        else:
            return "NORMAL", "CONTINUE"

    @staticmethod
    def _parametric_var(returns: np.ndarray, confidence: float) -> float:
        """參數化 VaR (假設正態分佈)"""
        from scipy import stats
        mean = np.mean(returns)
        std = np.std(returns)
        z_score = stats.norm.ppf(1 - confidence)
        return float(mean + z_score * std)
