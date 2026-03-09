#!/usr/bin/env python3
"""
Quant Engine v2 - Risk Guard Layer
=====================================
✅ 第一道防線 — 所有交易必須通過
✅ 倉位控制
✅ 日虧損控制
✅ 重複下單防護
✅ 熔斷機制
✅ 自動縮倉（InsufficientQty 解決方案）
"""

import logging
import state_store as ss
from config import RISK_CONFIG, NET_ASSETS

log = logging.getLogger("RiskGuard")


class RiskGuard:
    def __init__(self):
        self.cfg = RISK_CONFIG

    def check(
        self,
        state: dict,
        code: str,
        signal: str,
        price: float,
        available_cash: float,
        current_positions: dict,
    ) -> dict:
        """
        全面風控檢查
        回傳: {"ok": bool, "reason": str, "qty": int, "stop_loss": float, "stop_profit": float}
        """

        # ─── 1. 熔斷檢查 ───────────────────────────────────────
        if ss.is_circuit_broken(state):
            return self._reject("CircuitBroken")

        # ─── 2. 信號過濾 ───────────────────────────────────────
        if signal == "HOLD":
            return self._reject("HOLD")

        # ─── 3. 每日虧損上限 ───────────────────────────────────
        daily_pnl = ss.get_daily_pnl(state)
        net = NET_ASSETS
        loss_ratio = daily_pnl / net
        if loss_ratio < -self.cfg.max_daily_loss_pct:
            log.warning(
                f"🛑 每日虧損上限觸發: {daily_pnl:.2f} / {net:.2f} = {loss_ratio:.2%}"
            )
            # 觸發自動熔斷
            if not state.get("circuit_broken"):
                ss.set_circuit_break(state, True)
            return self._reject("MaxDailyLoss")

        # ─── 3.5 持倉保護 ──────────────────────────────────────
        if signal == "BUY":
            existing_pos = current_positions.get(code, {})
            existing_qty = existing_pos.get("qty", 0)

            # 空頭保護
            if existing_qty < 0:
                log.warning(f"⚠️ {code} 有空頭持倉（{existing_qty}股），跳過 BUY 直到空頭平倉")
                return self._reject("ShortPositionExists")

            # 已有多頭保護（避免無限加倉）
            if existing_qty > 0:
                log.info(f"⏸ {code} 已有多頭持倉（{existing_qty}股），跳過重複 BUY")
                return self._reject("AlreadyLong")

        # ─── 4. 倉位控制（只對 BUY 適用） ──────────────────────
        if signal == "BUY":
            # 計算現有總投入 (使用市值計算更準確)
            total_exposure_val = 0.0
            for pos in current_positions.values():
                # 優先使用 API 返回的市值，若無則用 (數量 * 當前價) 估算
                val = pos.get("market_val", 0)
                if val == 0:
                    val = pos.get("qty", 0) * pos.get("avg_cost", price)
                total_exposure_val += val

            total_exposure = total_exposure_val / net

            if total_exposure >= self.cfg.max_total_exposure:
                log.warning(
                    f"🛑 總倉位上限: {total_exposure:.2%} >= {self.cfg.max_total_exposure:.2%}"
                )
                return self._reject("MaxTotalExposure")

            # ─── 5. 計算下單量 ─────────────────────────────────
            # 單股最大金額
            max_spend = net * self.cfg.max_position_pct

            # 不能超過可用現金
            max_spend = min(max_spend, available_cash)

            if max_spend <= 0 or price <= 0:
                return self._reject("InsufficientCash")

            # 計算股數
            qty = int(max_spend / price)

            # 自動縮倉：確保至少買到 1 股
            if qty < self.cfg.min_qty:
                # 嘗試縮到 1 股
                if available_cash >= price:
                    qty = self.cfg.min_qty
                    log.warning(f"⚠️ 自動縮倉: 改為最小 {qty} 股")
                else:
                    return self._reject("InsufficientQty")

        elif signal == "SELL":
            # SELL 只需確認有可賣多頭持倉（qty > 0）
            pos = current_positions.get(code, {})
            qty = int(pos.get("qty", 0) or 0)
            if qty <= 0:
                # 空頭持倉不應再 SELL，避免加空；0 持倉則直接拒絕
                if qty < 0:
                    log.warning(f"⚠️ {code} 為空頭持倉（{qty}），拒絕 SELL 以避免加空")
                    return self._reject("ShortPositionExists")

                # 檢測是否有尚未同步的剛成交買單
                last_buy = state.get("last_orders", {}).get(code, {})
                import time as _time
                if last_buy.get("signal") == "BUY" and (_time.time() - last_buy.get("time", 0)) < 600:
                    log.warning(f"🕒 {code} 買單剛成交 ({int(_time.time()-last_buy['time'])}s前)，API 持倉可能尚未同步，拒絕 SELL 以防空投")
                    return self._reject("PositionSyncing")

                log.warning(f"⚠️ {code} 持倉檢查為 0，拒絕 SELL 信號。若此為異常，請檢查 API 同步。")
                return self._reject("NoPosition")

        else:
            return self._reject(f"UnknownSignal:{signal}")

        # ─── 6. 重複下單防護 ────────────────────────────────────
        if ss.is_duplicate_order(
            state, code, signal, qty, price, self.cfg.cooldown_seconds
        ):
            return self._reject("DuplicateOrder")

        # ─── 7. 計算止損止盈 ────────────────────────────────────
        stop_loss   = price * (1 - self.cfg.stop_loss_pct)
        stop_profit = price * (1 + self.cfg.stop_profit_pct)

        log.info(
            f"✅ 風控通過 | {code} {signal} {qty}股 @ ${price:.2f} | "
            f"SL: ${stop_loss:.2f} | TP: ${stop_profit:.2f}"
        )

        return {
            "ok": True,
            "qty": qty,
            "stop_loss": stop_loss,
            "stop_profit": stop_profit,
        }

    def record_error(self, state: dict):
        """記錄執行錯誤，達上限則熔斷"""
        count = ss.record_error(state)
        log.warning(f"⚠️ 連續錯誤: {count}/{self.cfg.max_consecutive_errors}")
        if count >= self.cfg.max_consecutive_errors:
            ss.set_circuit_break(state, True)
        ss.save(state)

    def record_success(self, state: dict):
        """成功下單後重置錯誤計數"""
        ss.reset_errors(state)
        ss.save(state)

    @staticmethod
    def _reject(reason: str) -> dict:
        log.warning(f"⛔ 風控拒絕: {reason}")
        return {"ok": False, "reason": reason, "qty": 0}
