"""
Intraday Data Fetcher - For Sudden Momentum Detection
"""

import pandas as pd
import yfinance as yf
from typing import Optional


class IntradayFetcher:
    """日內數據獲取器 (5分鐘/15分鐘/1小時)"""
    
    def __init__(self):
        pass
    
    def fetch(self, symbol: str, interval: str = '5m', 
              period: str = '7d') -> pd.DataFrame:
        """
        獲取日內數據
        """
        ticker = yf.Ticker(symbol)
        df = ticker.history(interval=interval, period=period)
        
        if df.empty:
            return pd.DataFrame()
        
        df = df.reset_index()
        
        # Keep only needed columns
        cols = ['Datetime', 'Open', 'High', 'Low', 'Close', 'Volume']
        df = df[[c for c in cols if c in df.columns]]
        
        return df
    
    def fetch_multiple(self, symbols: list, interval: str = '5m',
                      period: str = '7d') -> pd.DataFrame:
        """批量獲取多隻股票"""
        all_data = []
        
        for symbol in symbols:
            df = self.fetch(symbol, interval, period)
            if not df.empty:
                df['symbol'] = symbol
                all_data.append(df)
        
        if not all_data:
            return pd.DataFrame()
            
        return pd.concat(all_data, ignore_index=True)


class VolumeSpikeDetector:
    """成交量暴增檢測器"""
    
    def __init__(self, threshold: float = 2.0):
        self.threshold = threshold
        
    def detect(self, df: pd.DataFrame) -> pd.DataFrame:
        """檢測成交量暴增"""
        if 'Volume' not in df.columns:
            return df
            
        # 計算平均成交量 (20 periods)
        df['avg_volume'] = df['Volume'].rolling(20).mean()
        
        # 計算倍數
        df['volume_ratio'] = df['Volume'] / df['avg_volume']
        
        # 標記暴增
        df['volume_spike'] = df['volume_ratio'] > self.threshold
        
        return df
    
    def get_spikes(self, df: pd.DataFrame) -> list:
        """獲取暴增點"""
        if 'volume_spike' not in df.columns:
            return []
        return df[df['volume_spike'] == True].to_dict('records')


class MomentumIndicators:
    """動量指標 (適用於日內數據)"""
    
    @staticmethod
    def rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """計算 RSI"""
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        
        rs = gain / loss
        df[f'RSI_{period}'] = 100 - (100 / (1 + rs))
        
        return df
    
    @staticmethod
    def macd(df: pd.DataFrame, fast: int = 12, 
             slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """計算 MACD"""
        ema_fast = df['Close'].ewm(span=fast).mean()
        ema_slow = df['Close'].ewm(span=slow).mean()
        
        df[f'MACD_{fast}_{slow}'] = ema_fast - ema_slow
        df[f'MACD_sig_{fast}_{slow}_{signal}'] = df[f'MACD_{fast}_{slow}'].ewm(span=signal).mean()
        df[f'MACD_hist_{fast}_{slow}_{signal}'] = df[f'MACD_{fast}_{slow}'] - df[f'MACD_sig_{fast}_{slow}_{signal}']
        
        return df
    
    @staticmethod
    def vwap(df: pd.DataFrame) -> pd.DataFrame:
        """計算 VWAP"""
        tp = (df['High'] + df['Low'] + df['Close']) / 3
        df['VWAP'] = (tp * df['Volume']).cumsum() / df['Volume'].cumsum()
        
        return df
    
    @staticmethod
    def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """計算 ADX"""
        high_diff = df['High'].diff()
        low_diff = -df['Low'].diff()
        
        pos_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
        neg_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
        
        tr = pd.concat([
            df['High'] - df['Low'],
            abs(df['High'] - df['Close'].shift(1)),
            abs(df['Low'] - df['Close'].shift(1))
        ], axis=1).max(axis=1)
        
        atr = tr.rolling(period).mean()
        pos_di = 100 * pos_dm.rolling(period).mean() / atr
        neg_di = 100 * neg_dm.rolling(period).mean() / atr
        
        dx = 100 * abs(pos_di - neg_di) / (pos_di + neg_di)
        adx = dx.rolling(period).mean()
        
        df[f'ADX_{period}'] = adx
        df[f'ADX_pos_{period}'] = pos_di
        df[f'ADX_neg_{period}'] = neg_di
        
        return df


# Test
if __name__ == "__main__":
    fetcher = IntradayFetcher()
    
    print("=== Intraday Data Test ===")
    df = fetcher.fetch('NVDA', '5m', '7d')
    print(f"5-min data: {len(df)} rows")
    
    # Volume Spike
    detector = VolumeSpikeDetector(2.0)
    df = detector.detect(df)
    spikes = detector.get_spikes(df)
    print(f"Volume spikes: {len(spikes)}")
    
    # Momentum Indicators
    df = MomentumIndicators.rsi(df, 14)
    df = MomentumIndicators.macd(df)
    df = MomentumIndicators.vwap(df)
    df = MomentumIndicators.adx(df)
    
    print(df.tail(3))
