#!/usr/bin/env python3
"""
Market Regime Detection & Adaptive Strategy System
================================================
根據市場環境自動調整策略

功能：
1. 市場環境識別 (牛市/熊市/震盪/高波動)
2. 環境適配策略選擇
3. 動態倉位調整

Author: Kiro Quant V3
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd
import yfinance as yf

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("MarketRegime")


class MarketRegime(Enum):
    """市場環境類型"""
    BULL = "牛市"           # 上升趨勢
    BEAR = "熊市"           # 下降趨勢
    SIDEWAYS = "震盪市"     # 區間波動
    HIGH_VOL = "高波動"     # 波動加劇
    LOW_VOL = "低波動"      # 波動降低
    UNKNOWN = "未知"


@dataclass
class RegimeConfig:
    """環境配置"""
    # 趨勢識別參數
    trend_lookback: int = 50      # 趨勢回看天數
    sma_short: int = 20           # 短期均線
    sma_long: int = 50            # 長期均線
    
    # 波動性參數
    vol_lookback: int = 20       # 波動性回看天數
    vol_threshold_high: float = 1.5  # 高波動閾值 (相對於平均)
    vol_threshold_low: float = 0.7   # 低波動閾值
    
    # 趨勢強度閾值
    trend_strength_bull: float = 0.6  # 牛市的趨勢強度
    trend_strength_bear: float = -0.6 # 熊市的趨勢強度


class MarketRegimeDetector:
    """市場環境檢測器"""
    
    def __init__(self, config: RegimeConfig = None):
        self.config = config or RegimeConfig()
    
    def detect(self, df: pd.DataFrame) -> MarketRegime:
        """檢測當前市場環境"""
        if len(df) < self.config.trend_lookback:
            return MarketRegime.UNKNOWN
        
        close = df['Close'].values
        
        # 1. 檢測趨勢 (趨勢強度)
        trend = self._detect_trend(close)
        
        # 2. 檢測波動性
        vol_ratio = self._detect_volatility(close)
        
        # 3. 綜合判斷
        regime = self._combine_signals(trend, vol_ratio)
        
        return regime
    
    def _detect_trend(self, close: np.ndarray) -> float:
        """檢測趨勢強度 (-1 到 1)"""
        # Flatten if needed
        close = close.flatten() if close.ndim > 1 else close
        
        lookback = min(self.config.trend_lookback, len(close) - 1)
        
        # 簡單方法：比較近期價格與早期價格
        recent_price = np.mean(close[-lookback//2:])  # 最近半段平均
        early_price = np.mean(close[:lookback//2])    # 早期半段平均
        
        if early_price == 0:
            return 0
        
        # 計算漲跌幅
        change = (recent_price - early_price) / early_price
        
        # 標準化到 -1 到 1
        return np.clip(change * 5, -1, 1)  # 20% 漲幅 = 1.0
    
    def _detect_volatility(self, close: np.ndarray) -> float:
        """檢測波動性 (相對於歷史平均)"""
        # Flatten if needed
        close = close.flatten() if close.ndim > 1 else close
        
        lookback = min(self.config.vol_lookback, len(close) - 1)
        
        # 計算近期波動性 (日收益率標準差)
        returns = np.diff(close) / close[:-1]
        recent_vol = np.std(returns[-lookback:])
        
        # 計算歷史平均波動性
        hist_vol = np.std(returns)
        
        if hist_vol == 0:
            return 1.0
        
        return recent_vol / hist_vol
    
    def _combine_signals(self, trend: float, vol_ratio: float) -> MarketRegime:
        """綜合信號判斷環境"""
        
        # 優先判斷波動性
        if vol_ratio > self.config.vol_threshold_high:
            return MarketRegime.HIGH_VOL
        
        if vol_ratio < self.config.vol_threshold_low:
            return MarketRegime.LOW_VOL
        
        # 根據趨勢判斷
        if trend > self.config.trend_strength_bull:
            return MarketRegime.BULL
        elif trend < self.config.trend_strength_bear:
            return MarketRegime.BEAR
        else:
            return MarketRegime.SIDEWAYS
    
    def get_regime_info(self, df: pd.DataFrame) -> dict:
        """獲取詳細環境信息"""
        close = df['Close'].values
        
        trend = self._detect_trend(close)
        vol_ratio = self._detect_volatility(close)
        regime = self._combine_signals(trend, vol_ratio)
        
        return {
            'regime': regime.value,
            'trend_strength': round(trend, 3),
            'volatility_ratio': round(vol_ratio, 3),
            'recommendation': self._get_recommendation(regime)
        }
    
    def _get_recommendation(self, regime: MarketRegime) -> dict:
        """根據環境獲取策略建議"""
        
        recommendations = {
            MarketRegime.BULL: {
                'strategy': '動量策略 (Momentum)',
                'position': '80-100%',
                'actions': [
                    '追漲策略',
                    '趨勢跟隨',
                    '移動止損'
                ],
                'avoid': '均值回歸'
            },
            MarketRegime.BEAR: {
                'strategy': '均值回歸 / 空頭',
                'position': '0-20%',
                'actions': [
                    '搶反彈',
                    '設定止損',
                    '觀望為主'
                ],
                'avoid': '追漲'
            },
            MarketRegime.SIDEWAYS: {
                'strategy': '區間交易',
                'position': '30-50%',
                'actions': [
                    '高賣低買',
                    '支撐位買入',
                    '阻力位賣出'
                ],
                'avoid': '趨勢追蹤'
            },
            MarketRegime.HIGH_VOL: {
                'strategy': '保守策略',
                'position': '20-30%',
                'actions': [
                    '增大止損空間',
                    '減少倉位',
                    '觀望'
                ],
                'avoid': '重倉'
            },
            MarketRegime.LOW_VOL: {
                'strategy': '積極策略',
                'position': '60-80%',
                'actions': [
                    '可以加大倉位',
                    '趨勢突破策略',
                    '期權策略'
                ],
                'avoid': '過度保守'
            },
            MarketRegime.UNKNOWN: {
                'strategy': '觀望',
                'position': '0%',
                'actions': [
                    '等待確認'
                ],
                'avoid': '猜測'
            }
        }
        
        return recommendations[regime]


class AdaptiveStrategy:
    """自適應策略系統"""
    
    def __init__(self):
        self.detector = MarketRegimeDetector()
    
    def generate_signal(self, df: pd.DataFrame) -> dict:
        """根據市場環境生成交易信號"""
        
        # 1. 檢測市場環境
        regime_info = self.get_regime_info(df)
        regime = regime_info['regime']  # This is a string like "震盪市"
        
        # 2. 根據環境調整策略參數
        params = self._get_adaptive_params_from_string(regime)
        
        # 3. 生成基礎信號
        base_signal = self._generate_base_signal(df, params)
        
        # 4. 結合環境調整
        final_signal = self._adjust_for_regime(base_signal, regime_info)
        
        return {
            'regime': regime,
            'regime_info': regime_info,
            'params': params,
            'base_signal': base_signal,
            'final_signal': final_signal,
            'confidence': params.get('confidence', 0.5)
        }
    
    def _get_adaptive_params_from_string(self, regime: str) -> dict:
        """根據環境字符串獲取自適應參數"""
        
        params_map = {
            "牛市": {
                'rsi_oversold': 40,
                'rsi_overbought': 80,
                'position_pct': 0.8,
                'stop_loss': 0.03,
                'take_profit': 0.08,
                'confidence': 0.8
            },
            "熊市": {
                'rsi_oversold': 25,
                'rsi_overbought': 60,
                'position_pct': 0.2,
                'stop_loss': 0.015,
                'take_profit': 0.03,
                'confidence': 0.3
            },
            "震盪市": {
                'rsi_oversold': 30,
                'rsi_overbought': 70,
                'position_pct': 0.4,
                'stop_loss': 0.02,
                'take_profit': 0.04,
                'confidence': 0.6
            },
            "高波動": {
                'rsi_oversold': 25,
                'rsi_overbought': 75,
                'position_pct': 0.25,
                'stop_loss': 0.025,
                'take_profit': 0.05,
                'confidence': 0.4
            },
            "低波動": {
                'rsi_oversold': 35,
                'rsi_overbought': 65,
                'position_pct': 0.7,
                'stop_loss': 0.035,
                'take_profit': 0.10,
                'confidence': 0.7
            },
            "未知": {
                'rsi_oversold': 30,
                'rsi_overbought': 70,
                'position_pct': 0.0,
                'stop_loss': 0.02,
                'take_profit': 0.05,
                'confidence': 0.0
            }
        }
        
        return params_map.get(regime, params_map["未知"])
    
    def _get_adaptive_params(self, regime: MarketRegime) -> dict:
        """根據環境獲取自適應參數"""
        
        params_map = {
            MarketRegime.BULL: {
                'rsi_oversold': 40,      # 更嚴格的買入條件
                'rsi_overbought': 80,
                'position_pct': 0.8,
                'stop_loss': 0.03,       # 3% 止損
                'take_profit': 0.08,     # 8% 止盈
                'confidence': 0.8
            },
            MarketRegime.BEAR: {
                'rsi_oversold': 25,      # 超賣先買
                'rsi_overbought': 60,
                'position_pct': 0.2,
                'stop_loss': 0.015,     # 1.5% 止損
                'take_profit': 0.03,    # 3% 止盈
                'confidence': 0.3
            },
            MarketRegime.SIDEWAYS: {
                'rsi_oversold': 30,
                'rsi_overbought': 70,
                'position_pct': 0.4,
                'stop_loss': 0.02,
                'take_profit': 0.04,
                'confidence': 0.6
            },
            MarketRegime.HIGH_VOL: {
                'rsi_oversold': 25,
                'rsi_overbought': 75,
                'position_pct': 0.25,
                'stop_loss': 0.025,     # 增大止損
                'take_profit': 0.05,
                'confidence': 0.4
            },
            MarketRegime.LOW_VOL: {
                'rsi_oversold': 35,
                'rsi_overbought': 65,
                'position_pct': 0.7,
                'stop_loss': 0.035,
                'take_profit': 0.10,
                'confidence': 0.7
            },
            MarketRegime.UNKNOWN: {
                'rsi_oversold': 30,
                'rsi_overbought': 70,
                'position_pct': 0.0,
                'stop_loss': 0.02,
                'take_profit': 0.05,
                'confidence': 0.0
            }
        }
        
        return params_map[regime]
    
    def _generate_base_signal(self, df: pd.DataFrame, params: dict) -> int:
        """生成基礎信號 (不考慮環境)"""
        
        if len(df) < 30:
            return 0
        
        close = df['Close'].values
        
        # RSI
        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        # 信號
        if rsi < params['rsi_oversold']:
            return 1   # BUY
        elif rsi > params['rsi_overbought']:
            return -1  # SELL
        return 0      # HOLD
    
    def _adjust_for_regime(self, base_signal: int, regime_info: dict) -> int:
        """根據環境調整信號"""
        
        regime = regime_info['regime']
        confidence = regime_info['regime_info'].get('confidence', 0.5)
        
        # 在不明確的環境降低信號強度
        if confidence < 0.4 and base_signal != 0:
            # 降低信心，轉為觀望
            return 0
        
        # 熊市只做短線
        if regime == MarketRegime.BEAR.value and base_signal == 1:
            # 熊市減少買入信號
            if confidence < 0.5:
                return 0
        
        return base_signal
    
    def get_regime_info(self, df: pd.DataFrame) -> dict:
        """獲取環境信息"""
        return self.detector.get_regime_info(df)


# ============ 主程序 ============

def main():
    parser = argparse.ArgumentParser(description="Market Regime Detection & Adaptive Strategy")
    parser.add_argument("--symbol", default="NVDA", help="Stock symbol")
    parser.add_argument("--period", default="3mo", help="Data period (1mo, 3mo, 6mo, 1y)")
    args = parser.parse_args()
    
    logger.info("="*60)
    logger.info("🧠 Kiro Quant V3 - Market Regime Detection System")
    logger.info("="*60)
    logger.info(f"Symbol: {args.symbol}")
    
    # 獲取數據
    df = yf.download(args.symbol, period=args.period, progress=False)
    
    if df.empty:
        logger.error(f"No data for {args.symbol}")
        return
    
    logger.info(f"Loaded {len(df)} days of data")
    
    # 創建自適應策略
    strategy = AdaptiveStrategy()
    
    # 檢測市場環境
    regime_info = strategy.get_regime_info(df)
    
    logger.info("\n" + "="*60)
    logger.info(f"📊 市場環境分析: {args.symbol}")
    logger.info("="*60)
    logger.info(f"  當前環境: {regime_info['regime']}")
    logger.info(f"  趨勢強度: {regime_info['trend_strength']}")
    logger.info(f"  波動比率: {regime_info['volatility_ratio']}")
    
    rec = regime_info['recommendation']
    logger.info(f"\n🎯 策略建議:")
    logger.info(f"  推薦策略: {rec['strategy']}")
    logger.info(f"  建議倉位: {rec['position']}")
    logger.info(f"  建議動作: {', '.join(rec['actions'])}")
    logger.info(f"  避免動作: {rec['avoid']}")
    
    # 生成交易信號
    signal_result = strategy.generate_signal(df)
    
    signal_names = {1: "BUY 📈", 0: "HOLD ⚖️", -1: "SELL 📉"}
    
    logger.info(f"\n💡 交易信號:")
    logger.info(f"  基礎信號: {signal_names[signal_result['base_signal']]}")
    logger.info(f"  環境調整後: {signal_names[signal_result['final_signal']]}")
    logger.info(f"  信心指數: {signal_result['confidence']*100:.0f}%")
    
    # 顯示參數
    params = signal_result['params']
    logger.info(f"\n⚙️ 自適應參數:")
    logger.info(f"  RSI 買入閾值: {params['rsi_oversold']}")
    logger.info(f"  RSI 賣出閾值: {params['rsi_overbought']}")
    logger.info(f"  建議倉位: {params['position_pct']*100:.0f}%")
    logger.info(f"  止損: {params['stop_loss']*100:.1f}%")
    logger.info(f"  止盈: {params['take_profit']*100:.1f}%")
    
    logger.info("\n" + "="*60)
    logger.info("✅ Market Regime Detection Complete!")
    logger.info("="*60)


if __name__ == "__main__":
    main()
