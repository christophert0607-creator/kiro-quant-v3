#!/usr/bin/env python3
"""
Quant Engine v2 - Main Orchestrator
=====================================
✅ 統一入口
✅ 全自動交易循環
✅ RiskGuard 保護
✅ 安全 SIMULATE / LIVE 切換
✅ 可用真現金測試
"""

import futu as ft
import pandas as pd
import numpy as np
from xgboost import XGBClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import logging
from logging.handlers import RotatingFileHandler
import sys
import time
from datetime import datetime, timedelta

from config import (
    STOCK_LIST, KLINE_COUNT, RETRAIN_INTERVAL, LOOP_INTERVAL,
    RISK_CONFIG, OPEND_HOST, OPEND_PORT, TRADE_MODE, LOG_PATH,
    KLINE_CACHE_TTL_SECONDS, print_config
)
from execution_engine import ExecutionEngine
from massive_client import MassiveClient
from data_manager import DataManager
from notifier import send_tg_msg
import state_store as ss
from feature_engineering import generate_features, get_feature_cols

# ============================================================
# Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(LOG_PATH, maxBytes=1 * 1024 * 1024, backupCount=5, encoding="utf-8"),
    ]
)
log = logging.getLogger("QuantV2")


# Feature engineering has been decoupled to feature_engineering.py

# ============================================================
# Model
# ============================================================
class ModelManager:
    def __init__(self):
        self.models = {}    # code -> model
        self.scalers = {}   # code -> scaler
        self.log    = logging.getLogger("ModelManager")
        self._last_train = 0

    def train(self, code: str, df: pd.DataFrame) -> float:
        feature_cols = get_feature_cols()
        X = df[feature_cols].values
        y = df["target"].values
        
        # 確保有足夠數據
        if len(X) < 50:
            self.log.warning(f"⚠️ {code} 數據量不足 ({len(X)})，跳過訓練")
            return 0.0

        try:
            Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, shuffle=False)
            scaler = StandardScaler()
            Xtr = scaler.fit_transform(Xtr)
            Xte = scaler.transform(Xte)
            
            model = XGBClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                eval_metric="logloss", random_state=42,
                device="cpu"
            )
            model.fit(Xtr, ytr, eval_set=[(Xte, yte)], verbose=False)
            acc = accuracy_score(yte, model.predict(Xte))
            
            # Log feature importance for debugging
            try:
                importances = model.feature_importances_
                feat_imp = sorted(zip(feature_cols, importances), key=lambda x: x[1], reverse=True)[:10]
                self.log.info(f"📊 {code} Top features: {feat_imp[:3]}")
            except Exception:
                pass
            
            self.models[code] = model
            self.scalers[code] = scaler
            self._last_train = time.time()
            self.log.info(f"✅ {code} 訓練完成 | 準確率 {acc:.2%}")
            return acc
        except Exception as e:
            self.log.error(f"❌ {code} 訓練失敗: {e}", exc_info=True)
            return 0.0

    def predict(self, code: str, row: pd.Series) -> tuple:
        if code not in self.models or code not in self.scalers:
            return 0, 0.0
            
        feature_cols = get_feature_cols()
        x = self.scalers[code].transform(
            row[feature_cols].values.astype(float).reshape(1, -1)
        )
        pred = self.models[code].predict(x)[0]
        prob = self.models[code].predict_proba(x)[0]
        return int(pred), float(max(prob))

    def needs_retrain(self) -> bool:
        return (time.time() - self._last_train) > RETRAIN_INTERVAL

    def ready(self, code: str) -> bool:
        return code in self.models


# ============================================================
# Main Orchestrator
# ============================================================
class QuantV2:
    def __init__(self):
        self.log       = logging.getLogger("QuantV2")
        self.engine    = ExecutionEngine()
        self.model_mgr = ModelManager()
        self.data_manager = None
        self.kline_cache = {}

    def boot(self):
        print_config()
        self.log.info("🚀 Quant Engine v2 啟動中...")

        if not self.engine.connect():
            self.log.error("❌ 交易引擎連線失敗")
            return False

        # Quote Context：優先重用 execution engine 已建立連線，避免重複開連線
        self.quote_ctx = getattr(self.engine, "quote_ctx", None)
        if not self.quote_ctx:
            try:
                self.quote_ctx = ft.OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
            except Exception as e:
                self.log.warning(f"⚠️ Quote Context 失敗（將使用 Massive fallback）: {e}")

        # Massive Client（US 行情 fallback）
        self.massive = MassiveClient()
        
        # Data Manager (Centralized Routing)
        self.data_manager = DataManager(futu_trader=self.engine, massive_client=self.massive)
        
        self.log.info("✅ 系統就位！（含 DataManager 統一路由）")
        return True

    def fetch_kline(self, code: str) -> pd.DataFrame:
        """取 K 線：優先使用快取，否則透過 DataManager 獲取（含 Futu/YF/Massive 自動路由）"""
        now = time.time()
        cached = self.kline_cache.get(code)
        if cached:
            cached_at, cached_df = cached
            if now - cached_at <= KLINE_CACHE_TTL_SECONDS:
                self.log.info(f"💾 [Kline cache] {code} 命中，沿用 {len(cached_df)} 條")
                return cached_df.copy()

        if not self.data_manager:
            self.log.error("❌ DataManager 未初始化")
            return pd.DataFrame()

        # 透過 DataManager 獲取數據（內含 3 層 fallback）
        df = self.data_manager.get_history_kline(code, ktype="K_DAY", count=KLINE_COUNT)
        
        if not df.empty:
            self.kline_cache[code] = (now, df.copy())
            return df
            
        return pd.DataFrame()

    def train_models(self):
        self.log.info("\n⚙️  訓練模型...")
        for code in STOCK_LIST:
            df = self.fetch_kline(code)
            if df.empty:
                continue
            df = generate_features(df)
            if df.empty:
                continue
            acc = self.model_mgr.train(code, df)
            self.log.info(f"📊 {code} 訓練準確率: {acc:.2%}")

    def run_cycle(self):
        """單次交易循環"""
        state = ss.load()

        # 優先以 API 持倉同步本地快取，避免 state 舊值造成重複無效 SELL
        local_positions = state.get("positions", {}) if isinstance(state, dict) else {}
        try:
            api_positions = self.engine.get_positions()
            if isinstance(api_positions, dict):
                synced = {}
                for k, v in api_positions.items():
                    if isinstance(v, dict):
                        synced[k] = int(v.get("qty", 0) or 0)
                    else:
                        synced[k] = int(v or 0)
                local_positions = synced
        except Exception as e:
            self.log.warning(f"⚠️ 持倉同步失敗，回退至 state 快取: {e}")

        for code in STOCK_LIST:
            try:
                # 1. 取最新 K 線
                df = self.fetch_kline(code)
                if df.empty:
                    continue

                # 2. 生成特徵
                df = generate_features(df)
                if df.empty or not self.model_mgr.ready(code):
                    continue

                # 3. 生成信號
                row  = df.iloc[-1]
                price = float(row["close"])

                # ==== 強制止損 / 止盈檢查 (Absolute P&L Check) ====
                forced_sell = False
                pos_info = api_positions.get(code, {}) if isinstance(api_positions, dict) else {}
                qty = int(pos_info.get("qty", 0) or 0)
                avg_cost = float(pos_info.get("avg_cost", 0) or 0)
                
                if qty > 0 and avg_cost > 0:
                    pnl_pct = (price - avg_cost) / avg_cost
                    if pnl_pct <= -RISK_CONFIG.stop_loss_pct:
                        self.log.warning(f"🛑 {code} 觸發強制止損: {pnl_pct:.2%} <= -{RISK_CONFIG.stop_loss_pct:.2%}")
                        forced_sell = True
                    elif pnl_pct >= RISK_CONFIG.stop_profit_pct:
                        self.log.info(f"💰 {code} 觸發強制止盈: {pnl_pct:.2%} >= {RISK_CONFIG.stop_profit_pct:.2%}")
                        forced_sell = True

                if forced_sell:
                    signal = "SELL"
                    conf = 1.0
                else:
                    pred, conf = self.model_mgr.predict(code, row)
                    if conf < RISK_CONFIG.signal_confidence_threshold:
                        signal = "HOLD"
                    else:
                        signal = "BUY" if pred == 1 else "SELL"

                self.log.info(
                    f"📡 {code} | 信號: {signal} | "
                    f"信心: {conf:.2%} | 價格: ${price:.2f}"
                )

                if signal == "HOLD":
                    continue

                # 本地快速過濾：無可賣多頭持倉時跳過 SELL，減少無效 API 查詢與 log noise
                local_qty = int(local_positions.get(code, 0) or 0)
                if signal == "SELL" and local_qty <= 0:
                    self.log.info(f"⏭️ {code} 本地可賣持倉不足（qty={local_qty}），跳過 SELL")
                    continue

                # 4. 執行（RiskGuard 在內部）
                result = self.engine.execute(code=code, signal=signal, price=price)
                self.log.info(f"🔄 執行結果: {result}")

                # 同步本地持倉快取（避免同輪重複無效信號）
                if result.get("status") == "OK":
                    qty_delta = int(result.get("qty", 0) or 0)
                    if signal == "BUY":
                        local_positions[code] = int(local_positions.get(code, 0) or 0) + qty_delta
                    elif signal == "SELL":
                        local_positions[code] = max(0, int(local_positions.get(code, 0) or 0) - qty_delta)
                
                # Telegram 通知
                if result.get("status") == "OK":
                    msg = (
                        f"✅ *成交報信*\n"
                        f"品種: `{code}`\n"
                        f"動作: `{signal}`\n"
                        f"數量: `{result.get('qty')}`\n"
                        f"價格: `${result.get('price')}`\n"
                        f"單號: `{result.get('order_id')}`"
                    )
                    send_tg_msg(msg)
                elif result.get("status") == "FAIL":
                    msg = (
                        f"❌ *策略執行失敗*\n"
                        f"品種: `{code}`\n"
                        f"原因: `{result.get('reason')}`"
                    )
                    send_tg_msg(msg)

            except Exception as e:
                self.log.error(f"❌ {code} 週期錯誤: {e}", exc_info=True)

    def loop(self):
        """主持續循環"""
        self.log.info(f"\n🟢 進入持續交易循環 [{TRADE_MODE}]...\n")
        cycle = 0

        while True:
            try:
                cycle += 1
                self.log.info(
                    f"\n{'='*50}\n"
                    f"  第 {cycle} 輪 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {TRADE_MODE}\n"
                    f"{'='*50}"
                )

                # 重訓檢查
                if self.model_mgr.needs_retrain():
                    self.train_models()

                # 執行交易循環
                self.run_cycle()

                # 輪詢更新待成交訂單 (Order Polling)
                self.engine.sync_pending_orders()

                # 顯示當前持倉 + 狀態
                state = ss.load()
                self.log.info(
                    f"📊 今日狀態 | "
                    f"交易次數: {state.get('daily_trades', 0)} | "
                    f"今日 PnL: ${state.get('daily_pnl', 0):.2f} | "
                    f"熔斷: {state.get('circuit_broken', False)}"
                )

                self.log.info(f"💤 等待 {LOOP_INTERVAL}s...\n")
                time.sleep(LOOP_INTERVAL)

            except KeyboardInterrupt:
                self.log.info("🛑 收到停止指令，系統優雅停機")
                break
            except Exception as e:
                self.log.error(f"❌ 主循環錯誤: {e}", exc_info=True)
                time.sleep(30)

        self.shutdown()

    def shutdown(self):
        self.engine.disconnect()
        if self.data_manager:
            self.data_manager.stop()
        self.log.info("✅ Quant Engine v2 已完全停止")


# ============================================================
# Entry Point
# ============================================================
if __name__ == "__main__":
    bot = QuantV2()
    if bot.boot():
        bot.train_models()
        bot.loop()
    else:
        log.error("❌ 啟動失敗，請檢查 FutuOpenD 連線")
        sys.exit(1)
