#!/usr/bin/env python3
"""
Quant Engine v2 - Execution Engine
=====================================
✅ 所有下單必須通過 RiskGuard
✅ SIMULATE / LIVE 切換（改 config.py 唯一入口）
✅ 自動 retry + backoff
✅ 結構化錯誤分類
✅ 持倉同步
"""

import futu as ft
import logging
import time
import state_store as ss
from risk_guard_v36 import RiskGuardV36
from config import (
    OPEND_HOST, OPEND_PORT, TRADE_PWD, TRADE_MODE, MARKET, ORDER_COOLDOWN_HOURS,
    EMERGENCY_DAILY_LOSS_PCT, MIN_TOTAL_ASSETS_ALERT
)

# ──────────────────────────────────────────────────────────────
# Futu OrderStatus strings (as returned in order_list_query df)
# ──────────────────────────────────────────────────────────────
_TERMINAL_STATUSES = {
    "FILLED_ALL",        # 全部成交
    "CANCELLED_ALL",     # 已撤單
    "CANCELLED_PART",    # 部分成交後撤單
    "FAILED",            # 下單失敗
    "DELETED",           # 已刪除
    "DISABLED",
    "DEQUEUED",
}
_FILLED_STATUSES = {"FILLED_ALL", "FILLED_PART"}   # 全部或部分成交
_STALE_ORDER_TTL = 86400   # 24 小時後強制清除 pending 佇列


def _resolve_trade_context(host: str, port: int):
    """Create trade context by market; default to US for backward compatibility."""
    market = (MARKET or "US").upper()
    if market == "HK":
        return ft.OpenHKTradeContext(host=host, port=port)
    return ft.OpenUSTradeContext(host=host, port=port)

log = logging.getLogger("ExecutionEngine")


class ExecutionEngine:
    def __init__(self):
        self.risk = RiskGuardV36()
        self.trade_ctx = None
        self.quote_ctx = None
        self._connected = False

    def connect(self):
        try:
            # Quote Context
            self.quote_ctx = ft.OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)

            # Trade Context（按 MARKET 動態選擇）
            self.trade_ctx = _resolve_trade_context(host=OPEND_HOST, port=OPEND_PORT)

            if not TRADE_PWD:
                log.error("❌ 未設定 FUTU_TRADE_PWD，拒絕啟動交易引擎")
                self.disconnect()
                return False

            ret, msg = self.trade_ctx.unlock_trade(TRADE_PWD)
            if ret == ft.RET_OK:
                log.info(f"✅ 交易解鎖成功（{TRADE_MODE} 模式 | {MARKET}）")
            else:
                log.error(f"❌ 解鎖失敗: {msg}")
                self.disconnect()
                return False

            self._connected = True
            return True
        except Exception as e:
            log.error(f"❌ 連線失敗: {e}")
            self.disconnect()
            return False

    def disconnect(self):
        if self.quote_ctx:
            try: self.quote_ctx.close()
            except: pass
        if self.trade_ctx:
            try: self.trade_ctx.close()
            except: pass
        self._connected = False

    def get_account_info(self) -> dict:
        """查詢賬戶資金信息"""
        if not self.trade_ctx:
            return {}
        try:
            env = ft.TrdEnv.SIMULATE if TRADE_MODE == "SIMULATE" else ft.TrdEnv.REAL
            ret, data = self.trade_ctx.accinfo_query(trd_env=env)
            if ret == ft.RET_OK and not data.empty:
                row = data.iloc[0]
                return {
                    "cash": float(row.get("cash", 0)),
                    "total_assets": float(row.get("total_assets", 0)),
                    "power": float(row.get("power", 0)),
                }
        except Exception as e:
            log.error(f"❌ 賬戶查詢失敗: {e}")
        return {"cash": 0, "total_assets": 0, "power": 0}

    def get_positions(self) -> dict:
        """查詢當前持倉（code -> {qty, avg_cost}）"""
        if not self.trade_ctx:
            return {}
        try:
            env = ft.TrdEnv.SIMULATE if TRADE_MODE == "SIMULATE" else ft.TrdEnv.REAL
            ret, data = self.trade_ctx.position_list_query(trd_env=env)
            positions = {}
            if ret == ft.RET_OK:
                if data is not None and not data.empty:
                    for _, row in data.iterrows():
                        code = row.get("code", "")
                        # 過濾掉 0 股持倉
                        qty = int(row.get("qty", 0))
                        if qty == 0:
                            continue
                        positions[code] = {
                            "qty": qty,
                            "avg_cost": float(row.get("cost_price", 0)),
                            "market_val": float(row.get("market_val", 0)),
                            "pl_val": float(row.get("pl_val", 0)),
                        }
                        log.debug(
                            f"📊 持倉: {code} | "
                            f"數量: {positions[code]['qty']} | "
                            f"成本: ${positions[code]['avg_cost']:.2f} | "
                            f"盈虧: ${positions[code]['pl_val']:.2f}"
                        )
                # 更新 state.json 內的持倉狀態同步（僅變更時落盤，降低 I/O）
                state = ss.load()
                new_positions = {k: v["qty"] for k, v in positions.items()}
                if state.get("positions") != new_positions:
                    state["positions"] = new_positions
                    ss.save(state)
                return positions
            else:
                log.error(f"❌ 持倉查詢失敗: {data}")
        except Exception as e:
            log.error(f"❌ 持倉查詢例外: {e}")
        return {}

    def get_order_status(self, order_id: str) -> dict:
        """
        查詢單個訂單的成交狀態，回傳標準化字段：
        { order_id, code, signal, status, dealt_qty, dealt_avg_price }
        """
        if not self.trade_ctx:
            return {}
        try:
            env = ft.TrdEnv.SIMULATE if TRADE_MODE == "SIMULATE" else ft.TrdEnv.REAL
            ret, data = self.trade_ctx.order_list_query(
                order_id=str(order_id), trd_env=env
            )
            if ret == ft.RET_OK and data is not None and not data.empty:
                row = data.iloc[0]
                # Futu 回傳的 order_status 為字串，例如 "FILLED_ALL"
                raw_status = str(row.get("order_status", ""))
                # 部分成交時 dealt_qty 可能是 int 或 float
                dealt_qty = int(float(row.get("dealt_qty", 0) or 0))
                dealt_avg_price = float(row.get("dealt_avg_price", 0.0) or 0.0)
                log.debug(
                    f"🔍 [Poll] #{order_id} | status={raw_status} "
                    f"dealt={dealt_qty} avg_price={dealt_avg_price:.4f}"
                )
                return {
                    "order_id": str(row.get("order_id", order_id)),
                    "code": str(row.get("code", "")),
                    "status": raw_status,
                    "dealt_qty": dealt_qty,
                    "dealt_avg_price": dealt_avg_price,
                }
            else:
                log.warning(f"⚠️ [Poll] #{order_id} 查詢失敗或無數據: ret={ret}")
        except Exception as e:
            log.error(f"❌ 訂單查詢例外: {e}")
        return {}

    def sync_pending_orders(self):
        """
        從 state["pending_orders"] 輪詢所有待確認訂單，並執行：
          1. 全部成交 (FILLED_ALL)     → 計算實現 PnL，更新 state，發 TG 通知
          2. 終結狀態 (CANCELLED/FAIL) → 從佇列移除，發 TG 通知
          3. 過期訂單 (>24h未確認)     → 強制清除，告警
          4. 仍在途中                 → 保留至下次輪詢
        """
        state = ss.load()
        pending_orders: dict = state.get("pending_orders", {})
        if not pending_orders:
            return

        now = time.time()
        remaining_pending = {}
        state_changed = False

        for order_id, order_info in pending_orders.items():

            # ── 過期訂單保護：超過 24h 強制清除 ────────────────────
            age_secs = now - float(order_info.get("time", 0) or 0)
            if age_secs > _STALE_ORDER_TTL:
                log.warning(
                    f"⏰ [Poll] #{order_id} 已超過 {_STALE_ORDER_TTL/3600:.0f}h 未確認成交，"
                    f"強制從佇列移除（{order_info.get('code')} {order_info.get('signal')}）"
                )
                send_tg_msg(
                    f"⚠️ *訂單過期清除*\n"
                    f"單號: `{order_id}`\n"
                    f"品種: `{order_info.get('code')}`\n"
                    f"動作: `{order_info.get('signal')}`\n"
                    f"原因: 超過 24h 無成交確認，已自動移除"
                )
                state_changed = True
                continue

            # ── 向 Futu API 查詢最新狀態 ─────────────────────────────
            status_data = self.get_order_status(order_id)
            if not status_data:
                # 查詢失敗（可能是瞬時網絡問題），保留至下輪
                remaining_pending[order_id] = order_info
                continue

            status = status_data.get("status", "")
            code = status_data.get("code") or order_info.get("code", "")
            signal = order_info.get("signal", "")
            qty = status_data.get("dealt_qty", 0)
            avg_price = status_data.get("dealt_avg_price", 0.0)

            # ── 全部成交 ─────────────────────────────────────────────
            if status in _FILLED_STATUSES and qty > 0:
                state_changed = True
                log.info(
                    f"💹 [Poll] #{order_id} 成交確認: {signal} {qty}股 "
                    f"{code} @ ${avg_price:.4f}"
                )

                # 計算實現 PnL（只有 SELL 才有確定 PnL）
                if signal == "SELL":
                    order_cost = float(order_info.get("price", 0) or 0)
                    pnl_per_share = avg_price - order_cost
                    realized_pnl = round(pnl_per_share * qty, 4)
                    ss.update_daily_pnl(state, realized_pnl)
                    pnl_emoji = "🟢" if realized_pnl >= 0 else "🔴"
                    log.info(
                        f"{pnl_emoji} [Poll] {code} 已實現 PnL: "
                        f"${realized_pnl:+.2f} "
                        f"({pnl_per_share:+.4f}/股 × {qty}股)"
                    )
                    send_tg_msg(
                        f"{pnl_emoji} *成交確認 (SELL)*\n"
                        f"品種: `{code}`\n"
                        f"數量: `{qty}` 股\n"
                        f"成交均價: `${avg_price:.4f}`\n"
                        f"已實現盈虧: `${realized_pnl:+.2f}`\n"
                        f"單號: `{order_id}`"
                    )
                else:
                    # BUY 成交：記錄成交均價到 state 供後續 PnL 計算
                    fills = state.get("fills", {})
                    fills[code] = {
                        "qty": qty,
                        "avg_fill_price": avg_price,
                        "time": now,
                    }
                    state["fills"] = fills
                    send_tg_msg(
                        f"✅ *成交確認 (BUY)*\n"
                        f"品種: `{code}`\n"
                        f"數量: `{qty}` 股\n"
                        f"成交均價: `${avg_price:.4f}`\n"
                        f"單號: `{order_id}`"
                    )

            # ── 終結但非全成（撤單/失敗）────────────────────────────
            elif status in _TERMINAL_STATUSES:
                state_changed = True
                log.warning(
                    f"🚫 [Poll] #{order_id} 訂單終結 ({status}): "
                    f"{signal} {order_info.get('qty')}股 {code}"
                )
                send_tg_msg(
                    f"🚫 *訂單終結*\n"
                    f"品種: `{code}`\n"
                    f"動作: `{signal}`\n"
                    f"狀態: `{status}`\n"
                    f"單號: `{order_id}`"
                )

            # ── 仍在途 (SUBMITTING / WAITING_SUBMIT / SUBMITTED) ────
            else:
                remaining_pending[order_id] = order_info

        if state_changed:
            state["pending_orders"] = remaining_pending
            ss.save(state)
            log.info(
                f"🔄 [Poll] 佇列更新完成 "
                f"(剩餘待確認: {len(remaining_pending)} 筆)"
            )


    def emergency_liquidate_all(self) -> dict:
        """緊急賣出所有多頭持倉（市價單）。"""
        positions = self.get_positions()
        if not positions:
            return {"status": "SKIP", "reason": "NoPositions", "orders": []}

        results = []
        failed = []
        for code, pos in positions.items():
            qty = int(pos.get("qty", 0) or 0)
            if qty <= 0:
                continue
            result = self._place_sell_market_order(code=code, qty=qty)
            results.append(result)
            if result.get("status") != "OK":
                failed.append(code)

        if not results:
            return {"status": "SKIP", "reason": "NoLongPositions", "orders": []}

        if failed:
            return {"status": "PARTIAL", "reason": "SomeOrdersFailed", "orders": results, "failed": failed}
        return {"status": "OK", "orders": results}

    def check_emergency_triggers(self, state: dict | None = None) -> dict:
        """檢查並執行緊急條件（單日虧損、總資產閾值）。"""
        state = state or ss.load()
        account = self.get_account_info()
        total_assets = float(account.get("total_assets", 0) or 0)

        net_assets = max(total_assets, 1.0)
        daily_pnl = ss.get_daily_pnl(state)
        daily_loss_ratio = daily_pnl / net_assets

        alerts = []
        if total_assets and total_assets < MIN_TOTAL_ASSETS_ALERT:
            msg = f"⚠️ 總資產低於警戒線: ${total_assets:.2f} < ${MIN_TOTAL_ASSETS_ALERT:.2f}"
            log.warning(msg)
            alerts.append(msg)

        if daily_loss_ratio <= -EMERGENCY_DAILY_LOSS_PCT:
            log.critical(
                f"🛑 緊急平倉觸發: 單日虧損比率 {daily_loss_ratio:.2%} <= -{EMERGENCY_DAILY_LOSS_PCT:.2%}"
            )
            liquidate_result = self.emergency_liquidate_all()
            ss.set_circuit_break(state, True)
            ss.save(state)
            return {
                "triggered": True,
                "reason": "MaxDailyLossEmergency",
                "daily_loss_ratio": daily_loss_ratio,
                "alerts": alerts,
                "liquidation": liquidate_result,
            }

        return {
            "triggered": False,
            "daily_loss_ratio": daily_loss_ratio,
            "alerts": alerts,
            "total_assets": total_assets,
        }

    def execute(self, code: str, signal: str, price: float) -> dict:
        """
        核心執行函數
        1. 從 state 取狀態
        2. 查賬戶資金 + 持倉
        3. 過 RiskGuard
        4. 下單
        5. 更新 state
        """
        state = ss.load()

        # 1. 查賬戶資金
        acct = self.get_account_info()
        available_cash = acct.get("cash", 0)
        total_assets = float(acct.get("total_assets", 0) or 0)
        
        # 實盤且資金過低（例如 < $1000）時警告
        if TRADE_MODE == "LIVE" and available_cash < 1000:
            log.warning(f"📉 實盤資金警戒: ${available_cash:.2f}")

        # 僅 BUY 需要現金門檻；SELL 應允許在低現金時執行（例如減倉/平倉）
        if signal == "BUY" and available_cash <= 100:
            log.warning(f"⚠️ 現金不足 (${available_cash:.2f})，跳過 BUY")
            return {"status": "SKIP", "reason": "NoCash"}

        # 2. 查持倉
        positions = self.get_positions()

        # 2.5 如果是 BUY 且有空頭，先平倉（失敗則快速返回，避免後續無效風控/下單）
        if signal == "BUY":
            if not self.close_short_if_needed(code, positions):
                log.warning(f"⛔ {code} 空頭平倉失敗，跳過本輪 BUY")
                return {"status": "FAIL", "reason": "CloseShortFailed"}
            positions = self.get_positions()  # 重新取持倉

        # 2.6 本地訂單追蹤：若今日已對此 code 下過 BUY，跳過（盤後防重複）
        if signal == "BUY":
            last = state.get("last_orders", {}).get(code, {})
            last_signal = last.get("signal", "")
            last_time = last.get("time", 0)
            import time as _time
            # 同一交易日內已有 BUY 紀錄 → 跳過（冷卻時間可配置，默認 1 小時）
            cooldown_secs = ORDER_COOLDOWN_HOURS * 3600
            if last_signal == "BUY" and (_time.time() - last_time) < cooldown_secs:
                log.info(f"⏸ {code} 今日已下 BUY（{int((_time.time()-last_time)/60)} 分鐘前），等待成交確認")
                return {"status": "SKIP", "reason": "OrderPending"}

        # 3. 風控檢查
        risk_result = self.risk.check(
            state=state,
            code=code,
            signal=signal,
            price=price,
            available_cash=available_cash,
            current_positions=positions,
            net_assets=total_assets,
        )

        if not risk_result.get("ok"):
            return {"status": "REJECTED", "reason": risk_result.get("reason")}

        qty = risk_result["qty"]

        # 4. 下單（帶 retry）
        result = self._place_with_retry(code, signal, qty, price)

        # 5. 更新 state
        if result.get("status") == "OK":
            order_id = result.get('order_id')
            ss.record_order(state, code, signal, qty, price)
            
            # 寫入待確認訂單佇列
            pending_orders = state.get("pending_orders", {})
            pending_orders[order_id] = {
                "code": code,
                "signal": signal,
                "qty": qty,
                "price": price,
                "time": time.time()
            }
            state["pending_orders"] = pending_orders
            
            self.risk.record_success(state)
            log.info(f"✅ 下單成功: {signal} {qty}股 {code} @ ${price:.2f} -> #{order_id}")
        else:
            self.risk.record_error(state)

        ss.save(state)
        return result

    def _place_with_retry(
        self, code: str, signal: str, qty: int, price: float,
        max_retries: int = 3
    ) -> dict:
        """帶 exponential backoff 的下單"""
        env = ft.TrdEnv.SIMULATE if TRADE_MODE == "SIMULATE" else ft.TrdEnv.REAL
        side = ft.TrdSide.BUY if signal == "BUY" else ft.TrdSide.SELL

        for attempt in range(1, max_retries + 1):
            try:
                log.info(
                    f"📤 [{TRADE_MODE}] {signal} {qty}股 {code} @ ${price:.2f} "
                    f"(嘗試 {attempt}/{max_retries})"
                )

                if not self.trade_ctx:
                    return {"status": "FAIL", "reason": "NoTradeContext"}

                ret, data = self.trade_ctx.place_order(
                    price=price,
                    qty=qty,
                    code=code,
                    trd_side=side,
                    order_type=ft.OrderType.NORMAL,
                    trd_env=env,
                )

                if ret == ft.RET_OK:
                    order_id = data["order_id"].values[0]
                    return {
                        "status": "OK",
                        "order_id": order_id,
                        "mode": TRADE_MODE,
                        "code": code,
                        "signal": signal,
                        "qty": qty,
                        "price": price,
                    }
                else:
                    err_str = str(data)
                    log.error(f"❌ 下單失敗 (嘗試 {attempt}): {err_str[:80]}")
                    # 分類錯誤 — PERMANENT 不重試
                    error_type = self._classify_error(err_str)
                    if error_type == "PERMANENT":
                        log.warning(f"🚫 永久性錯誤，停止重試: {err_str[:60]}")
                        return {"status": "FAIL", "reason": err_str[:80]}

                    # 可重試錯誤（最後一次失敗不再無意義等待）
                    if attempt < max_retries:
                        wait = 2 ** attempt
                        log.info(f"🔄 {wait}s 後重試...")
                        time.sleep(wait)

            except Exception as e:
                log.error(f"❌ 下單例外 (嘗試 {attempt}): {e}")
                if attempt < max_retries:
                    time.sleep(2 ** attempt)

        return {"status": "FAIL", "reason": "MaxRetriesExceeded"}


    def _place_sell_market_order(self, code: str, qty: int) -> dict:
        env = ft.TrdEnv.SIMULATE if TRADE_MODE == "SIMULATE" else ft.TrdEnv.REAL
        if not self.trade_ctx:
            return {"status": "FAIL", "code": code, "reason": "NoTradeContext"}

        try:
            ret, data = self.trade_ctx.place_order(
                price=0,
                qty=qty,
                code=code,
                trd_side=ft.TrdSide.SELL,
                order_type=ft.OrderType.MARKET,
                trd_env=env,
            )
            if ret == ft.RET_OK:
                order_id = data["order_id"].values[0]
                log.warning(f"🚨 緊急平倉下單成功: SELL {qty} {code} -> #{order_id}")
                return {"status": "OK", "code": code, "qty": qty, "order_id": order_id}
            return {"status": "FAIL", "code": code, "qty": qty, "reason": str(data)[:120]}
        except Exception as e:
            return {"status": "FAIL", "code": code, "qty": qty, "reason": str(e)}

    def close_short_if_needed(self, code: str, positions: dict) -> bool:
        """如果持有空頭，先平倉"""
        pos = positions.get(code, {})
        qty = pos.get("qty", 0)
        if qty < 0:  # 空頭持倉
            abs_qty = abs(int(qty))
            log.warning(f"⚠️ {code} 有空頭 {qty} 股，先平倉...")
            env = ft.TrdEnv.SIMULATE if TRADE_MODE == "SIMULATE" else ft.TrdEnv.REAL
            try:
                ret, data = self.trade_ctx.place_order(
                    price=0,
                    qty=abs_qty,
                    code=code,
                    trd_side=ft.TrdSide.BUY,
                    order_type=ft.OrderType.MARKET,
                    trd_env=env,
                )
                if ret == ft.RET_OK:
                    log.info(f"✅ 空頭平倉下單成功: {code} {abs_qty}股，等待撮合...")
                    time.sleep(3)  # 等待成交與狀態更新
                    return True
                else:
                    log.error(f"❌ 空頭平倉失敗: {data}")
                    return False
            except Exception as e:
                log.error(f"❌ 平倉例外: {e}")
                return False
        return True  # 無空頭，直接返回 OK

    @staticmethod
    def _classify_error(error_msg: str) -> str:
        """分類錯誤類型"""
        permanent_keywords = [
            "InsufficientQty", "InvalidCode", "TradingHalted",
            "MarketClosed", "InvalidPrice", "InvalidQty",
            "持有空头", "空头", "购买力不足", "资金不足"
        ]
        for kw in permanent_keywords:
            if kw.lower() in error_msg.lower():
                return "PERMANENT"
        return "RETRYABLE"
