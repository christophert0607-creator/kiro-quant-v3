#!/usr/bin/env python3
"""
V3.6 — 市場狀態識別 (根據第七章)
===================================
不同策略適用於不同市場狀態:

| 策略類型   | 適合市況 | 持有期  |
|-----------|---------|--------|
| 均值回歸   | 震盪市   | 1-5 日  |
| 動量       | 趨勢市   | 5-20 日 |
| 突破       | 趨勢市   | 5-30 日 |
| 配對       | 任何     | 1-10 日 |

市場狀態:
- trending_up:    上升趨勢
- trending_down:  下降趨勢
- sideways:       震盪市
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("V36.Regime")


@dataclass
class RegimeConfig:
    """市場狀態識別配置"""
    lookback: int = 20                # 回看期
    trend_threshold: float = 1.5      # 趨勢強度門檻
    volatility_high: float = 0.025    # 高波動門檻 (日)
    volatility_low: float = 0.01      # 低波動門檻 (日)
    adx_period: int = 14              # ADX 計算週期
    adx_trend_threshold: float = 25   # ADX > 25 = 趨勢市


# 策略映射表
STRATEGY_MAP = {
    "trending_up":   ["momentum", "breakout"],
    "trending_down": ["mean_reversion", "momentum_short"],
    "sideways":      ["mean_reversion", "pair_trading"],
    "high_volatility": ["mean_reversion"],
    "low_volatility":  ["breakout"],
}


class MarketRegimeDetector:
    """
    市場狀態識別器

    根據 Ernest Chan 書籍 第七章:
    Different strategies work in different market regimes.
    """

    def __init__(self, config: Optional[RegimeConfig] = None):
        self.config = config or RegimeConfig()

    def detect_regime(
        self,
        prices: List[float],
        lookback: Optional[int] = None,
    ) -> Dict:
        """
        識別當前市場狀態

        參數:
        - prices: 價格序列
        - lookback: 回看期

        返回:
        - dict: {regime, trend_strength, volatility, recommended_strategies}
        """
        lb = lookback or self.config.lookback

        if len(prices) < lb + 1:
            return {
                "regime": "unknown",
                "trend_strength": 0.0,
                "volatility": 0.0,
                "recommended_strategies": ["momentum"],
                "reason": "insufficient_data",
            }

        prices_arr = np.array(prices[-lb - 1:], dtype=float)
        returns = np.diff(prices_arr) / prices_arr[:-1]

        # 趨勢強度 = |平均收益| / 波動性
        mean_return = np.mean(returns)
        std_return = np.std(returns) if np.std(returns) > 0 else 1e-9
        trend_strength = abs(mean_return) / std_return

        # 波動性
        volatility = float(std_return)

        # 判定市場狀態
        if trend_strength > self.config.trend_threshold:
            if mean_return > 0:
                regime = "trending_up"
            else:
                regime = "trending_down"
        else:
            regime = "sideways"

        # 波動性修飾
        if volatility > self.config.volatility_high:
            vol_regime = "high_volatility"
        elif volatility < self.config.volatility_low:
            vol_regime = "low_volatility"
        else:
            vol_regime = "normal_volatility"

        # 推薦策略
        recommended = STRATEGY_MAP.get(regime, ["momentum"])

        result = {
            "regime": regime,
            "volatility_regime": vol_regime,
            "trend_strength": round(trend_strength, 4),
            "mean_return": round(float(mean_return), 6),
            "volatility": round(volatility, 6),
            "recommended_strategies": recommended,
        }

        log.info(
            f"Market Regime: {regime} (strength={trend_strength:.4f}, "
            f"vol={volatility:.4%}) → {recommended}"
        )

        return result

    def detect_with_adx(
        self,
        high: List[float],
        low: List[float],
        close: List[float],
    ) -> Dict:
        """
        使用 ADX (Average Directional Index) 判定趨勢

        ADX > 25 = 趨勢市
        ADX < 25 = 震盪市
        +DI > -DI = 上升趨勢
        -DI > +DI = 下降趨勢
        """
        period = self.config.adx_period

        if len(close) < period * 2:
            return self.detect_regime(close)

        h = np.array(high, dtype=float)
        l = np.array(low, dtype=float)
        c = np.array(close, dtype=float)

        # True Range
        tr_list = []
        for i in range(1, len(c)):
            tr = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
            tr_list.append(tr)

        # +DM, -DM
        plus_dm = []
        minus_dm = []
        for i in range(1, len(c)):
            up = h[i] - h[i-1]
            down = l[i-1] - l[i]
            plus_dm.append(up if up > down and up > 0 else 0)
            minus_dm.append(down if down > up and down > 0 else 0)

        # 平滑
        tr_arr = np.array(tr_list, dtype=float)
        pdm_arr = np.array(plus_dm, dtype=float)
        mdm_arr = np.array(minus_dm, dtype=float)

        atr = self._smooth(tr_arr, period)
        smoothed_pdm = self._smooth(pdm_arr, period)
        smoothed_mdm = self._smooth(mdm_arr, period)

        # +DI, -DI
        plus_di = 100 * smoothed_pdm / (atr + 1e-9)
        minus_di = 100 * smoothed_mdm / (atr + 1e-9)

        # DX, ADX
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
        adx = self._smooth(dx, period)

        last_adx = float(adx[-1]) if len(adx) > 0 else 0
        last_pdi = float(plus_di[-1]) if len(plus_di) > 0 else 0
        last_mdi = float(minus_di[-1]) if len(minus_di) > 0 else 0

        if last_adx > self.config.adx_trend_threshold:
            if last_pdi > last_mdi:
                regime = "trending_up"
            else:
                regime = "trending_down"
        else:
            regime = "sideways"

        recommended = STRATEGY_MAP.get(regime, ["momentum"])

        return {
            "regime": regime,
            "adx": round(last_adx, 2),
            "plus_di": round(last_pdi, 2),
            "minus_di": round(last_mdi, 2),
            "recommended_strategies": recommended,
        }

    def select_strategy(self, regime: str) -> List[str]:
        """根據市場狀態選擇策略"""
        return STRATEGY_MAP.get(regime, ["momentum"])

    @staticmethod
    def _smooth(data: np.ndarray, period: int) -> np.ndarray:
        """Wilder 平滑法"""
        if len(data) < period:
            return data
        result = np.zeros_like(data)
        result[period - 1] = np.sum(data[:period])
        for i in range(period, len(data)):
            result[i] = result[i - 1] - result[i - 1] / period + data[i]
        return result[period - 1:]
