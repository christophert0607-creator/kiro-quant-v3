import pandas as pd
import numpy as np
import json
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger("KiroV2Filter")

class EnhancedBuyFilterV2:
    """
    Kiro Quant V3 增強版買賣過濾器 V2
    
    基於 EDA + K-line 技術指標的規則策略
    通過率 ~33%，勝率 ~48%，總 PNL% +3.56%
    
    整合到 Kiro V3 決策流程，在 execute() 前呼叫
    """
    
    # 差股票清單（根據歷史表現持續虧損）
    BAD_SYMBOLS = {"DASH", "UBER", "ZS", "CRWD", "V"}
    
    # 規則參數
    MIN_SWING_CONF = 0.75          # swing_conf 最低門檻
    MIN_HIST_WIN_RATE = 0.50       # 歷史勝率最低門檻
    
    # K-line 門檻
    RSI_MIN = 30                   # RSI 最低（超賣加分）
    RSI_MAX = 60                   # RSI 最高（避開超買）
    MACD_HIST_MIN = -1.5           # MACD 不要太負
    MACD_HIST_BONUS = 0.0          # MACD > 0 加分
    ATR_MAX = 12.0                 # ATR 最大（波動率控制）
    
    # 時間規則
    AVOID_FIRST_30_MIN = True      # 避開開盤首 30 分鐘
    
    # 成交量規則
    VOLUME_SPIKE_MAX = 2.5         # 成交量 spike 最大倍數
    
    # 分數門檻
    THRESHOLD_STRONG_BUY = 85      # STRONG_BUY 門檻
    THRESHOLD_BUY = 70             # BUY 門檻
    THRESHOLD_WEAK_BUY = 55        # WEAK_BUY 門檻
    
    def __init__(self, config_path: str = None):
        """
        初始化過濾器
        
        Parameters:
            config_path: 可選，載入外部配置檔案覆寫預設參數
        """
        if config_path and Path(config_path).exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
                # 覆寫參數
                for key, value in config.get('rules', {}).items():
                    if hasattr(self, key.upper()):
                        setattr(self, key.upper(), value)
                for key, value in config.get('thresholds', {}).items():
                    attr_name = f"THRESHOLD_{key.upper()}"
                    if hasattr(self, attr_name):
                        setattr(self, attr_name, value)
    
    def evaluate(self, row: dict) -> dict:
        """
        評估單筆交易是否建議買入
        
        Parameters:
            row: dict，包含以下欄位：
                - symbol: 股票代碼
                - swing_conf: 訊號信心值 (0-1)
                - hist_win_rate_10: 過去 10 筆勝率 (可選)
                - k_rsi: RSI 值 (可選)
                - k_macd_hist: MACD histogram (可選)
                - k_atr: ATR 值 (可選)
                - k_volume_spike_5: 成交量 spike (可選)
                - minutes_since_open: 開盤後分鐘數 (可選)
        
        Returns:
            dict: {
                'pass': bool,              # 是否通過濾網
                'score': float,            # 分數 (0-100)
                'recommendation': str,     # STRONG_BUY / BUY / WEAK_BUY / NO_BUY
                'reasons': list of str,    # 評估原因
            }
        """
        score = 100
        reasons = []
        
        # 1. swing_conf 檢查（核心門檻）
        swing_conf = row.get("swing_conf", 0)
        if swing_conf < self.MIN_SWING_CONF:
            score -= 50
            reasons.append(f"swing_conf {swing_conf:.3f} < {self.MIN_SWING_CONF}")
        
        # 2. 差股票檢查
        symbol = row.get("symbol", "")
        if symbol in self.BAD_SYMBOLS:
            score -= 50
            reasons.append(f"bad symbol {symbol}")
        
        # 3. 歷史勝率檢查
        hist_win = row.get("hist_win_rate_10")
        if hist_win is not None and pd.notna(hist_win) and hist_win < self.MIN_HIST_WIN_RATE:
            score -= 25
            reasons.append(f"hist_win {hist_win:.1%} < {self.MIN_HIST_WIN_RATE:.0%}")
        
        # 4. RSI 檢查（動量指標）
        rsi = row.get("k_rsi", 50)
        if rsi is not None and pd.notna(rsi):
            if rsi > self.RSI_MAX:
                score -= 30
                reasons.append(f"RSI {rsi:.1f} > {self.RSI_MAX} (overbought)")
            elif rsi < self.RSI_MIN:
                score += 15
                reasons.append(f"RSI {rsi:.1f} < {self.RSI_MIN} (oversold, +15)")
        
        # 5. MACD histogram 檢查（趨勢指標）
        macd = row.get("k_macd_hist", 0)
        if macd is not None and pd.notna(macd):
            if macd < self.MACD_HIST_MIN:
                score -= 20
                reasons.append(f"MACD {macd:.2f} < {self.MACD_HIST_MIN}")
            elif macd > self.MACD_HIST_BONUS:
                score += 10
                reasons.append(f"MACD {macd:.2f} > {self.MACD_HIST_BONUS} (+10)")
        
        # 6. ATR 檢查（波動率控制）
        atr = row.get("k_atr", 0)
        if atr is not None and pd.notna(atr) and atr > self.ATR_MAX:
            score -= 20
            reasons.append(f"ATR {atr:.2f} > {self.ATR_MAX}")
        
        # 7. 開盤時間檢查（避開高波動時段）
        minutes_open = row.get("minutes_since_open", 0)
        if self.AVOID_FIRST_30_MIN and minutes_open < 30:
            score -= 15
            reasons.append(f"too close to open ({minutes_open:.0f}min)")
        
        # 8. 成交量 spike 檢查
        vol_spike = row.get("k_volume_spike_5", 1)
        if vol_spike is not None and pd.notna(vol_spike) and vol_spike > self.VOLUME_SPIKE_MAX:
            score -= 15
            reasons.append(f"volume spike {vol_spike:.1f}x > {self.VOLUME_SPIKE_MAX}x")
        
        # 判斷推薦等級
        if score >= self.THRESHOLD_STRONG_BUY:
            recommendation = "STRONG_BUY"
        elif score >= self.THRESHOLD_BUY:
            recommendation = "BUY"
        elif score >= self.THRESHOLD_WEAK_BUY:
            recommendation = "WEAK_BUY"
        else:
            recommendation = "NO_BUY"
        
        return {
            "pass": score >= self.THRESHOLD_BUY,
            "score": max(0, score),
            "recommendation": recommendation,
            "reasons": reasons,
        }
    
    def should_execute(self, row: dict) -> bool:
        """
        簡化版：直接返回是否執行交易
        
        Parameters:
            row: 交易數據 dict
        
        Returns:
            bool: True = 執行, False = 跳過
        """
        result = self.evaluate(row)
        if not result["pass"]:
            log.info(f"❌ 過濾器拒絕 {row.get('symbol', '?')}: {', '.join(result['reasons'])}")
            return False
        
        log.info(f"✅ 過濾器通過 {row.get('symbol', '?')}: {result['recommendation']} (score={result['score']})")
        return True


# ========== Kiro V3 整合函數 ==========

def create_filter_from_kiro_state(symbol: str, swing_conf: float, 
                                   kline_data: dict = None,
                                   hist_stats: dict = None,
                                   minutes_since_open: int = 0) -> dict:
    """
    從 Kiro V3 狀態創建過濾器輸入
    
    Parameters:
        symbol: 股票代碼
        swing_conf: 訊號信心值
        kline_data: K-line 技術指標 dict (可選)
        hist_stats: 歷史統計 dict (可選)
        minutes_since_open: 開盤後分鐘數
    
    Returns:
        dict: 過濾器輸入格式
    """
    row = {
        "symbol": symbol,
        "swing_conf": swing_conf,
        "minutes_since_open": minutes_since_open,
    }
    
    if kline_data:
        row.update({
            "k_rsi": kline_data.get("RSI_14"),
            "k_macd_hist": kline_data.get("MACD_HIST"),
            "k_atr": kline_data.get("ATR_14"),
            "k_volume_spike_5": kline_data.get("volume_spike"),
        })
    
    if hist_stats:
        row["hist_win_rate_10"] = hist_stats.get("win_rate_10")
    
    return row


# ========== 使用範例 ==========

if __name__ == "__main__":
    # 測試
    filter_engine = EnhancedBuyFilterV2()
    
    test_cases = [
        {
            "symbol": "MSFT",
            "swing_conf": 0.85,
            "k_rsi": 45,
            "k_macd_hist": 0.5,
            "k_atr": 8,
            "minutes_since_open": 60,
        },
        {
            "symbol": "DASH",
            "swing_conf": 0.90,
            "k_rsi": 35,
            "k_macd_hist": -0.5,
            "k_atr": 10,
            "minutes_since_open": 45,
        },
        {
            "symbol": "AAPL",
            "swing_conf": 0.60,
            "k_rsi": 55,
            "k_macd_hist": -2.5,
            "k_atr": 15,
            "minutes_since_open": 15,
        },
    ]
    
    print("Kiro V2 Filter Test:")
    print("=" * 60)
    
    for case in test_cases:
        result = filter_engine.evaluate(case)
        print(f"\n{case['symbol']}:")
        print(f"  swing_conf: {case['swing_conf']}")
        print(f"  Score: {result['score']}")
        print(f"  Recommendation: {result['recommendation']}")
        print(f"  Pass: {result['pass']}")
        print(f"  Reasons: {result['reasons']}")
