"""
Technical Indicators Module

支持: RSI, MACD, BB, KDJ, ADX, CCI, WILLR, ATR
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Callable
from abc import ABC, abstractmethod


class BaseIndicator(ABC):
    """指標基類"""
    
    name: str = ""
    
    @abstractmethod
    def calculate(self, df: pd.DataFrame, **params) -> pd.DataFrame:
        """計算指標"""
        pass
    
    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, **params) -> pd.Series:
        """生成信號: 1=buy, -1=sell, 0=hold"""
        pass


class RSIIndicator(BaseIndicator):
    """Relative Strength Index"""
    name = "RSI"
    
    def calculate(self, df: pd.DataFrame, **params) -> pd.DataFrame:
        period = params.get('period', 14)
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        df[f'RSI_{period}'] = rsi
        return df
    
    def generate_signal(self, df: pd.DataFrame, **params) -> pd.Series:
        period = params.get('period', 14)
        oversold = params.get('oversold', 30)
        overbought = params.get('overbought', 70)
        
        rsi = df[f'RSI_{period}']
        
        signal = pd.Series(0, index=df.index)
        signal[rsi < oversold] = 1   # Buy
        signal[rsi > overbought] = -1  # Sell
        signal[rsi.between(oversold, overbought)] = 0  # Hold
        
        return signal


class MACDIndicator(BaseIndicator):
    """Moving Average Convergence Divergence"""
    name = "MACD"
    
    def calculate(self, df: pd.DataFrame, **params) -> pd.DataFrame:
        fast = params.get('fast', 12)
        slow = params.get('slow', 26)
        signal = params.get('signal', 9)
        
        ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
        
        df[f'MACD_{fast}_{slow}'] = ema_fast - ema_slow
        df[f'MACD_signal_{fast}_{slow}_{signal}'] = df[f'MACD_{fast}_{slow}'].ewm(span=signal, adjust=False).mean()
        df[f'MACD_hist_{fast}_{slow}_{signal}'] = df[f'MACD_{fast}_{slow}'] - df[f'MACD_signal_{fast}_{slow}_{signal}']
        
        return df
    
    def generate_signal(self, df: pd.DataFrame, **params) -> pd.Series:
        fast = params.get('fast', 12)
        slow = params.get('slow', 26)
        signal = params.get('signal', 9)
        
        macd = df[f'MACD_{fast}_{slow}']
        macd_signal = df[f'MACD_signal_{fast}_{slow}_{signal}']
        
        signal = pd.Series(0, index=df.index)
        signal[macd > macd_signal] = 1   # Bullish
        signal[macd < macd_signal] = -1  # Bearish
        
        return signal


class BBIndicator(BaseIndicator):
    """Bollinger Bands"""
    name = "BB"
    
    def calculate(self, df: pd.DataFrame, **params) -> pd.DataFrame:
        period = params.get('period', 20)
        std = params.get('std', 2.0)
        
        sma = df['close'].rolling(window=period).mean()
        std_dev = df['close'].rolling(window=period).std()
        
        df[f'BB_upper_{period}_{std}'] = sma + (std_dev * std)
        df[f'BB_middle_{period}_{std}'] = sma
        df[f'BB_lower_{period}_{std}'] = sma - (std_dev * std)
        
        return df
    
    def generate_signal(self, df: pd.DataFrame, **params) -> pd.Series:
        period = params.get('period', 20)
        std = params.get('std', 2.0)
        
        upper = df[f'BB_upper_{period}_{std}']
        lower = df[f'BB_lower_{period}_{std}']
        close = df['close']
        
        signal = pd.Series(0, index=df.index)
        signal[close < lower] = 1   # Oversold - Buy
        signal[close > upper] = -1  # Overbought - Sell
        
        return signal


class KDJIndicator(BaseIndicator):
    """Stochastic Oscillator (KDJ)"""
    name = "KDJ"
    
    def calculate(self, df: pd.DataFrame, **params) -> pd.DataFrame:
        period = params.get('period', 14)
        k_period = params.get('k', 3)
        d_period = params.get('d', 3)
        
        low_min = df['low'].rolling(window=period).min()
        high_max = df['high'].rolling(window=period).max()
        
        k = 100 * (df['close'] - low_min) / (high_max - low_min)
        d = k.rolling(window=d_period).mean()
        j = 3 * k - 2 * d
        
        df[f'KDJ_K_{period}'] = k
        df[f'KDJ_D_{period}'] = d
        df[f'KDJ_J_{period}'] = j
        
        return df
    
    def generate_signal(self, df: pd.DataFrame, **params) -> pd.Series:
        period = params.get('period', 14)
        
        k = df[f'KDJ_K_{period}']
        
        signal = pd.Series(0, index=df.index)
        signal[k < 20] = 1   # Oversold - Buy
        signal[k > 80] = -1  # Overbought - Sell
        
        return signal


class ADXIndicator(BaseIndicator):
    """Average Directional Index"""
    name = "ADX"
    
    def calculate(self, df: pd.DataFrame, **params) -> pd.DataFrame:
        period = params.get('period', 14)
        
        high_diff = df['high'].diff()
        low_diff = -df['low'].diff()
        
        pos_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
        neg_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
        
        tr = pd.concat([
            df['high'] - df['low'],
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        ], axis=1).max(axis=1)
        
        atr = tr.rolling(window=period).mean()
        pos_di = 100 * pos_dm.rolling(window=period).mean() / atr
        neg_di = 100 * neg_dm.rolling(window=period).mean() / atr
        
        dx = 100 * abs(pos_di - neg_di) / (pos_di + neg_di)
        adx = dx.rolling(window=period).mean()
        
        df[f'ADX_{period}'] = adx
        df[f'ADX_pos_{period}'] = pos_di
        df[f'ADX_neg_{period}'] = neg_di
        
        return df
    
    def generate_signal(self, df: pd.DataFrame, **params) -> pd.Series:
        period = params.get('period', 14)
        
        adx = df[f'ADX_{period}']
        
        signal = pd.Series(0, index=df.index)
        signal[adx > 25] = 1   # Strong trend - follow direction
        
        return signal


class CCIIndicator(BaseIndicator):
    """Commodity Channel Index"""
    name = "CCI"
    
    def calculate(self, df: pd.DataFrame, **params) -> pd.DataFrame:
        period = params.get('period', 20)
        
        tp = (df['high'] + df['low'] + df['close']) / 3
        sma = tp.rolling(window=period).mean()
        mad = tp.rolling(window=period).apply(lambda x: np.abs(x - x.mean()).mean())
        
        cci = (tp - sma) / (0.015 * mad)
        
        df[f'CCI_{period}'] = cci
        return df
    
    def generate_signal(self, df: pd.DataFrame, **params) -> pd.Series:
        period = params.get('period', 20)
        
        cci = df[f'CCI_{period}']
        
        signal = pd.Series(0, index=df.index)
        signal[cci < -100] = 1   # Oversold - Buy
        signal[cci > 100] = -1   # Overbought - Sell
        
        return signal


class WILLRIndicator(BaseIndicator):
    """Williams %R"""
    name = "WILLR"
    
    def calculate(self, df: pd.DataFrame, **params) -> pd.DataFrame:
        period = params.get('period', 14)
        
        highest = df['high'].rolling(window=period).max()
        lowest = df['low'].rolling(window=period).min()
        
        willr = -100 * (highest - df['close']) / (highest - lowest)
        
        df[f'WILLR_{period}'] = willr
        return df
    
    def generate_signal(self, df: pd.DataFrame, **params) -> pd.Series:
        period = params.get('period', 14)
        
        willr = df[f'WILLR_{period}']
        
        signal = pd.Series(0, index=df.index)
        signal[willr < -80] = 1   # Oversold - Buy
        signal[willr > -20] = -1  # Overbought - Sell
        
        return signal


class ATRIndicator(BaseIndicator):
    """Average True Range - for position sizing"""
    name = "ATR"
    
    def calculate(self, df: pd.DataFrame, **params) -> pd.DataFrame:
        period = params.get('period', 14)
        
        tr = pd.concat([
            df['high'] - df['low'],
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        ], axis=1).max(axis=1)
        
        atr = tr.rolling(window=period).mean()
        
        df[f'ATR_{period}'] = atr
        return df
    
    def generate_signal(self, df: pd.DataFrame, **params) -> pd.Series:
        # ATR 主要用於倉位管理，唔直接產生信號
        return pd.Series(0, index=df.index)


# Indicator Registry
INDICATORS = {
    'RSI': RSIIndicator,
    'MACD': MACDIndicator,
    'BB': BBIndicator,
    'KDJ': KDJIndicator,
    'ADX': ADXIndicator,
    'CCI': CCIIndicator,
    'WILLR': WILLRIndicator,
    'ATR': ATRIndicator,
}


def get_indicator(name: str) -> BaseIndicator:
    """獲取指標實例"""
    indicator_class = INDICATORS.get(name.upper())
    if not indicator_class:
        raise ValueError(f"Unknown indicator: {name}")
    return indicator_class()
