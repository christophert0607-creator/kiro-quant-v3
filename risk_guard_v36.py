#!/usr/bin/env python3
"""
V3.6 整合升級 — risk_guard_v36.py
====================================
在原有 RiskGuard 基礎上整合 V3.6 新功能:
- Kelly Formula 動態倉位 (取代固定 max_position_pct)
- 交易成本門檻檢查 (預期收益 < 成本 → 拒絕)
- VaR/CVaR 日風險觸發
- 市場狀態識別 → 動態策略選擇
"""

import logging
import time as _time
from typing import Dict, Optional, List

import state_store as ss
from config import RISK_CONFIG, NET_ASSETS
from v36 import (
    KellyPositionSizer, TransactionCostCalculator,
    RiskMetrics, MarketRegimeDetector, load_v36_config
)
from v36.kelly_position_sizer import KellyConfig
from v36.risk_metrics import RiskControlConfig

log = logging.getLogger("RiskGuardV36")


class RiskGuardV36:
    """
    V3.6 升級版風控守衛

    在原有 RiskGuard 邏輯的基礎上加入:
    1. Kelly Formula 動態倉位計算
    2. 交易成本可行性檢查
    3. VaR/CVaR 日風險觸發
    4. 市場狀態感知
    """

    def __init__(self, v36_config_path: Optional[str] = None):
        self.cfg = RISK_CONFIG
        self.v36 = load_v36_config(v36_config_path)

        # 初始化 V3.6 模組
        self.kelly = KellyPositionSizer(KellyConfig(
            kelly_factor=self.v36.kelly_factor,
            max_position_per_trade=self.v36.max_position_per_trade,
            max_total_position=self.v36.max_total_position,
            max_positions=self.v36.max_positions,
            risk_per_trade=self.v36.risk_per_trade,
            max_daily_risk=self.v36.max_daily_risk,
        ))
        self.cost_calc = TransactionCostCalculator()
        self.risk_metrics = RiskMetrics(RiskControlConfig(
            var_confidence=self.v36.var_confidence,
            max_drawdown_stop=self.v36.max_drawdown_stop,
            max_drawdown_pause=self.v36.max_drawdown_pause,
            max_drawdown_kill=self.v36.max_drawdown_kill,
        ))
        self.regime_detector = MarketRegimeDetector()

        log.info(f"✅ RiskGuardV36 初始化 (Kelly={self.v36.kelly_factor}, "
                 f"MaxPos={self.v36.max_position_per_trade:.0%})")

    def check(
        self,
        state: dict,
        code: str,
        signal: str,
        price: float,
        available_cash: float,
        current_positions: dict,
        net_assets: Optional[float] = None,
        # V3.6 新增參數
        win_rate: float = 0.55,
        avg_win: float = 0.0,
        avg_loss: float = 0.0,
        expected_return: float = 0.0,
        market_cap: str = "large_cap",
        recent_returns: Optional[List[float]] = None,
        price_history: Optional[List[float]] = None,
    ) -> dict:
        """
        V3.6 增強版全面風控檢查

        新增:
        - Kelly 動態倉位
        - 交易成本可行性
        - VaR 日風險
        - 市場狀態感知
        """

        # ─── 1. 熔斷檢查 (沿用原邏輯) ───────────────────────────
        if ss.is_circuit_broken(state):
            return self._reject("CircuitBroken")

        # ─── 2. 信號過濾 ─────────────────────────────────────────
        if signal == "HOLD":
            return self._reject("HOLD")

        # ─── 3. 每日虧損上限 ─────────────────────────────────────
        daily_pnl = ss.get_daily_pnl(state)
        net = float(net_assets or NET_ASSETS)
        if net <= 0:
            net = float(NET_ASSETS)

        loss_ratio = daily_pnl / net
        if loss_ratio < -self.cfg.max_daily_loss_pct:
            log.warning(f"🛑 每日虧損上限觸發: {loss_ratio:.2%}")
            if not state.get("circuit_broken"):
                ss.set_circuit_break(state, True)
            return self._reject("MaxDailyLoss")

        # ─── 3.5 VaR 日風險檢查 (V3.6 新增) ─────────────────────
        if recent_returns and len(recent_returns) >= 20:
            daily_risk_check = self.risk_metrics.check_daily_risk(
                daily_pnl, recent_returns, net
            )
            action = daily_risk_check.get("action", "CONTINUE")
            if action == "EMERGENCY_LIQUIDATE":
                ss.set_circuit_break(state, True)
                ss.save(state)
                return self._reject("CVaR_EmergencyStop")
            elif action == "PAUSE_TRADING":
                return self._reject("VaR_PausedTrading")

        # ─── 4. BUY 信號特定檢查 ─────────────────────────────────
        if signal == "BUY":

            # 4a. 持倉保護 (沿用原邏輯)
            existing_qty = current_positions.get(code, {}).get("qty", 0)
            if existing_qty < 0:
                return self._reject("ShortPositionExists")
            if existing_qty > 0:
                return self._reject("AlreadyLong")

            # 4b. 交易成本可行性檢查 (V3.6 新增)
            if expected_return > 0:
                cost_check = self.cost_calc.is_profitable_after_cost(
                    expected_return=expected_return,
                    symbol=code,
                    quantity=max(1, int(net * 0.05 / price)) if price > 0 else 1,
                    price=price,
                    market_cap=market_cap,
                )
                if not cost_check["profitable"]:
                    log.warning(
                        f"⛔ [{code}] 交易成本過高: 預期收益={expected_return:.4%} "
                        f"< 成本={cost_check['round_trip_cost_with_buffer']:.4%}"
                    )
                    return self._reject("CostTooHigh")

            # 4c. 市場狀態感知 (V3.6 新增，僅 log 不阻擋)
            if price_history and len(price_history) >= 20:
                regime = self.regime_detector.detect_regime(price_history)
                log.info(
                    f"📊 市場狀態: {regime['regime']} "
                    f"(推薦策略: {regime['recommended_strategies']})"
                )
                state.setdefault("v36_regime", {})[code] = regime["regime"]

            # 4d. 總倉位上限
            total_exposure_val = sum(
                pos.get("market_val", 0) or pos.get("qty", 0) * pos.get("avg_cost", price)
                for pos in current_positions.values()
            )
            total_exposure = total_exposure_val / net
            if total_exposure >= self.v36.max_total_position:
                return self._reject("MaxTotalExposure")

            # 4e. Kelly 動態倉位計算 (V3.6 新增) ──────────────────
            current_positions_count = len([p for p in current_positions.values()
                                           if p.get("qty", 0) > 0])
            daily_risk_used = max(0.0, -loss_ratio)

            if avg_win > 0 and avg_loss > 0:
                # 有歷史資料 → Kelly 計算
                kelly_result = self.kelly.calculate_position_size(
                    win_rate=win_rate,
                    avg_win=avg_win,
                    avg_loss=avg_loss,
                    portfolio_value=net,
                    tier="verified",
                    current_total_exposure=total_exposure,
                    current_positions_count=current_positions_count,
                    daily_risk_used=daily_risk_used,
                )

                if kelly_result["reason"] != "ok" or kelly_result["position_value"] <= 0:
                    return self._reject(f"Kelly_{kelly_result['reason']}")

                max_spend = kelly_result["position_value"]
                log.info(
                    f"💰 Kelly 倉位: ${max_spend:,.2f} "
                    f"({kelly_result['pct_of_portfolio']:.2%}) "
                    f"[raw={kelly_result['kelly_raw']:.4f}]"
                )
            else:
                # 無歷史資料 → 回退到保守固定比例 (5%)
                max_spend = net * min(self.v36.max_position_per_trade, 0.05)
                log.info(f"💰 固定倉位 (無歷史資料): ${max_spend:,.2f}")

            # 不超過可用現金
            max_spend = min(max_spend, available_cash)

            if max_spend <= 0 or price <= 0:
                return self._reject("InsufficientCash")

            qty = int(max_spend / price)

            # 自動縮倉
            if qty < self.cfg.min_qty:
                if available_cash >= price:
                    qty = self.cfg.min_qty
                    log.warning(f"⚠️ 自動縮倉 → {qty} 股")
                else:
                    return self._reject("InsufficientQty")

        elif signal == "SELL":
            pos = current_positions.get(code, {})
            qty = int(pos.get("qty", 0) or 0)
            if qty <= 0:
                if qty < 0:
                    return self._reject("ShortPositionExists")
                last_buy = state.get("last_orders", {}).get(code, {})
                if last_buy.get("signal") == "BUY" and (_time.time() - last_buy.get("time", 0)) < 600:
                    return self._reject("PositionSyncing")
                return self._reject("NoPosition")
        else:
            return self._reject(f"UnknownSignal:{signal}")

        # ─── 5. 重複下單防護 ─────────────────────────────────────
        if ss.is_duplicate_order(state, code, signal, qty, price, self.cfg.cooldown_seconds):
            return self._reject("DuplicateOrder")

        # ─── 6. 止損止盈計算 (沿用原邏輯) ───────────────────────
        stop_loss = price * (1 - self.cfg.stop_loss_pct)
        stop_profit = price * (1 + self.cfg.stop_profit_pct)

        log.info(
            f"✅ V3.6 風控通過 | {code} {signal} {qty}股 @ ${price:.2f} | "
            f"SL: ${stop_loss:.2f} | TP: ${stop_profit:.2f}"
        )

        return {
            "ok": True,
            "qty": qty,
            "stop_loss": stop_loss,
            "stop_profit": stop_profit,
        }

    def record_error(self, state: dict):
        count = ss.record_error(state)
        log.warning(f"⚠️ 連續錯誤: {count}/{self.cfg.max_consecutive_errors}")
        if count >= self.cfg.max_consecutive_errors:
            ss.set_circuit_break(state, True)
        ss.save(state)

    def record_success(self, state: dict):
        ss.reset_errors(state)
        ss.save(state)

    @staticmethod
    def _reject(reason: str) -> dict:
        log.warning(f"⛔ V3.6 風控拒絕: {reason}")
        return {"ok": False, "reason": reason, "qty": 0}
