#!/usr/bin/env python3
"""
Feature Engineering Module
==========================
✅ 獨立模組負責特徵生成，解耦主循環
✅ 包含 Schema Validation 處理，預防缺失特徵導致模型崩潰
"""

import pandas as pd
import numpy as np
import logging

log = logging.getLogger("FeatureEngineering")

_FEATURE_COLS = []

def get_feature_cols() -> list:
    """獲取當前使用的特徵列表"""
    global _FEATURE_COLS
    return _FEATURE_COLS.copy()

def generate_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    接收 K 線數據 DataFrame，計算技術指標，並更新 _FEATURE_COLS
    回傳計算完特徵的 DataFrame。若數據異常會嘗試修復，無法修復則回傳 empty DataFrame。
    """
    df = df.copy()
    
    # ─── 1. Schema Validation & Repair ────────────────────────
    required_cols = ["close", "high", "low", "volume", "open"]
    for col in required_cols:
        if col not in df.columns:
            log.warning(f"⚠️ Missing required column for feature engineering: {col}")
            # 如果連 close 都沒有，無法計算，直接回傳空
            if col == "close":
                log.error("❌ 無法修復：缺少 'close' 欄位。")
                return pd.DataFrame()
            
            # 嘗試修復其他必要欄位
            if col == "volume":
                df[col] = 0.0
            elif col in ["open", "high", "low"]:
                df[col] = df["close"]

    # 確保不會有中斷的 nan 影響滾動計算
    df.ffill(inplace=True)
    df.bfill(inplace=True)

    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # ─── 2. Feature Calculations ─────────────────────────────
    # MA
    for n in [5, 10, 20, 60]:
        df[f"MA{n}"]       = c.rolling(n).mean()
        df[f"MA{n}_ratio"] = c / df[f"MA{n}"] - 1

    # RSI
    d = c.diff()
    roll_up = d.where(d > 0, 0).rolling(14).mean()
    roll_down = -d.where(d < 0, 0).rolling(14).mean()
    df["RSI"] = 100 - 100 / (1 + roll_up / (roll_down + 1e-9))
    
    # MACD
    e12, e26       = c.ewm(12).mean(), c.ewm(26).mean()
    df["MACD"]     = e12 - e26
    df["MACD_sig"] = df["MACD"].ewm(9).mean()
    df["MACD_h"]   = df["MACD"] - df["MACD_sig"]

    # Bollinger Bands
    ma20 = c.rolling(20).mean()
    sd20 = c.rolling(20).std()
    df["BB_pos"]    = (c - (ma20 - 2 * sd20)) / (4 * sd20 + 1e-9)
    df["Vol_ratio"] = v / (v.rolling(5).mean() + 1e-9)

    # Candlestick shapes
    rng = h - l + 1e-9
    df["Body"]   = (df["close"] - df["open"]) / rng
    df["UpShad"] = (h - df[["close", "open"]].max(axis=1)) / rng
    df["DnShad"] = (df[["close", "open"]].min(axis=1) - l) / rng

    # ATR
    tr = pd.concat([h - l, abs(h - c.shift()), abs(l - c.shift())], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    df["ATR_Ratio"] = df["ATR"] / c

    # OBV
    close_diff = c.diff().fillna(0)
    signed_vol = np.where(close_diff > 0, v, np.where(close_diff < 0, -v, 0))
    df["OBV"] = pd.Series(signed_vol, index=df.index).cumsum()

    # Stochastic Oscillator
    lowest_low     = l.rolling(14).min()
    highest_high   = h.rolling(14).max()
    df["Stoch_K"]  = 100 * (c - lowest_low) / (highest_high - lowest_low + 1e-9)
    df["Stoch_D"]  = df["Stoch_K"].rolling(3).mean()
    
    # ─── 3. 後置空值填補 (Schema Validation for Generated Features)
    # 把因為 rolling 產生的前緣 nan 補前值或0
    df.fillna(0, inplace=True)

    # ─── 4. Target Labeling ───────────────────────────────
    df["target"] = (c.shift(-1) > c).astype(int)
    
    # 放棄最後一筆（因為 target 無法計算）
    df.dropna(inplace=True)

    # ─── 5. Update FEATURE_COLS ────────────────────────────
    drop = {
        "time_key", "open", "high", "low", "close", "volume", "turnover",
        "change_rate", "last_close", "target", "code", "stock_owner", "name",
        "stock_name", "pe_annual", "pe_ttm", "pb_ratio", "net_profit_ratio",
        "return_on_equity", "update_time", "suspension_info"
    }
    
    global _FEATURE_COLS
    # 每次計算後保證 _FEATURE_COLS 固定，以便後續存取
    _FEATURE_COLS = sorted([
        col for col in df.columns
        if col not in drop and pd.api.types.is_numeric_dtype(df[col])
    ])

    return df
