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
    print_config
)
from execution_engine import ExecutionEngine
from massive_client import MassiveClient
from notifier import send_tg_msg
import state_store as ss

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


# ============================================================
# Feature Engineering（保留原有指標）
# ============================================================
FEATURE_COLS = []

def generate_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    for n in [5, 10, 20, 60]:
        df[f"MA{n}"]       = c.rolling(n).mean()
        df[f"MA{n}_ratio"] = c / df[f"MA{n}"] - 1

    d = c.diff()
    df["RSI"] = 100 - 100 / (
        1 + d.where(d > 0, 0).rolling(14).mean()
          / (-d.where(d < 0, 0).rolling(14).mean() + 1e-9)
    )
    e12, e26       = c.ewm(12).mean(), c.ewm(26).mean()
    df["MACD"]     = e12 - e26
    df["MACD_sig"] = df["MACD"].ewm(9).mean()
    df["MACD_h"]   = df["MACD"] - df["MACD_sig"]

    ma20 = c.rolling(20).mean()
    sd20 = c.rolling(20).std()
    df["BB_pos"]    = (c - (ma20 - 2 * sd20)) / (4 * sd20 + 1e-9)
    df["Vol_ratio"] = v / (v.rolling(5).mean() + 1e-9)

    rng = h - l + 1e-9
    df["Body"]   = (df["close"] - df["open"]) / rng
    df["UpShad"] = (h - df[["close", "open"]].max(axis=1)) / rng
    df["DnShad"] = (df[["close", "open"]].min(axis=1) - l) / rng

    tr = pd.concat([h - l, abs(h - c.shift()), abs(l - c.shift())], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["ATR_Ratio"] = df["ATR"] / c

    # OBV（向量化，避免逐行 Python 迴圈）
    close_diff = c.diff().fillna(0)
    signed_vol = np.where(close_diff > 0, v, np.where(close_diff < 0, -v, 0))
    df["OBV"] = pd.Series(signed_vol, index=df.index).cumsum()

    lowest_low     = l.rolling(14).min()
    highest_high   = h.rolling(14).max()
    df["Stoch_K"]  = 100 * (c - lowest_low) / (highest_high - lowest_low + 1e-9)
    df["Stoch_D"]  = df["Stoch_K"].rolling(3).mean()

    df["target"] = (c.shift(-1) > c).astype(int)
    df.dropna(inplace=True)

    drop = {
        "time_key", "open", "high", "low", "close", "volume", "turnover",
        "change_rate", "last_close", "target", "code", "stock_owner", "name",
        "stock_name", "pe_annual", "pe_ttm", "pb_ratio", "net_profit_ratio",
        "return_on_equity", "update_time", "suspension_info"
    }
    global FEATURE_COLS
    FEATURE_COLS = [
        col for col in df.columns
        if col not in drop and pd.api.types.is_numeric_dtype(df[col])
    ]
    return df


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
        X = df[FEATURE_COLS].values
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
                feat_imp = sorted(zip(FEATURE_COLS, importances), key=lambda x: x[1], reverse=True)[:10]
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
            
        x = self.scalers[code].transform(
            row[FEATURE_COLS].values.astype(float).reshape(1, -1)
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
        self.quote_ctx = None
        self.massive   = None

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
        self.log.info("✅ 系統就位！（含 Massive fallback）")
        return True

    def fetch_kline(self, code: str) -> pd.DataFrame:
        """取 K 線：先嘗試 Futu，失敗自動 fallback 至 Massive"""
        # 1. Futu 優先
        if self.quote_ctx:
            try:
                ret, data, msg = self.quote_ctx.request_history_kline(
                    code, ktype=ft.KLType.K_DAY, max_count=KLINE_COUNT
                )
                if ret == ft.RET_OK and not data.empty:
                    self.log.info(f"📥 [Futu] {code} K線 {len(data)} 條")
                    return data
                else:
                    self.log.warning(f"⚠️ Futu K線無數據或失敗 ({code}): {msg}")
            except Exception as e:
                self.log.warning(f"⚠️ Futu K線例外: {e}")

        # 2. Massive fallback（US 股）
        symbol = code.replace("US.", "")
        self.log.info(f"🔄 [Massive fallback] 取 {symbol} K線...")
        try:
            from_date = (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d')
            to_date   = datetime.now().strftime('%Y-%m-%d')
            bars = self.massive.get_kline(symbol, timespan="day", limit=KLINE_COUNT,
                                          from_date=from_date, to_date=to_date)
            if bars:
                df = pd.DataFrame(bars)
                # Massive 返回最新在前，要反轉
                df = df.sort_values("time_key").reset_index(drop=True)
                self.log.info(f"📥 [Massive] {code} K線 {len(df)} 條")
                return df
        except Exception as e:
            self.log.error(f"❌ Massive K線失敗: {e}")

        self.log.error(f"❌ {code} 無法取得 K 線（Futu + Massive 均失敗）")
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
                pred, conf = self.model_mgr.predict(code, row)
                price = float(row["close"])

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
        if self.quote_ctx:
            self.quote_ctx.close()
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
