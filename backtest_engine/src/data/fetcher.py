"""
Data Fetcher - Multiple Data Sources (Polars)

支持: yfinance, Polars native
"""

import polars as pl
import yfinance as yf
from typing import List, Optional
from abc import ABC, abstractmethod


class BaseFetcher(ABC):
    """數據獲取器基類"""
    
    @abstractmethod
    def fetch(self, symbol: str, start_date: str, end_date: str) -> pl.DataFrame:
        pass
    
    def fetch_multiple(self, symbols: List[str], market: str,
                      start_date: str, end_date: str) -> pl.DataFrame:
        """批量獲取"""
        all_data = []
        
        for symbol in symbols:
            yahoo_symbol = self._to_yahoo_format(symbol, market)
            
            try:
                df = self.fetch(yahoo_symbol, start_date, end_date)
                if df is not None and not df.is_empty():
                    df = df.with_columns(pl.lit(symbol).alias('symbol'))
                    all_data.append(df)
            except Exception as e:
                print(f"⚠️ Failed to fetch {symbol}: {e}")
                
        if not all_data:
            return pl.DataFrame()
            
        return pl.concat(all_data)
    
    @abstractmethod
    def _to_yahoo_format(self, symbol: str, market: str) -> str:
        pass


class YFinanceFetcher(BaseFetcher):
    """Yahoo Finance 數據獲取器 (直接轉 Polars)"""
    
    def fetch(self, symbol: str, start_date: str, end_date: str) -> pl.DataFrame:
        """從 Yahoo Finance 獲取 -> 直接轉 Polars"""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date)
            
            if df.empty:
                return pl.DataFrame()
            
            # Reset index -> Polars
            df = df.reset_index()
            df = pl.from_pandas(df)
            
            # Rename columns
            df = df.rename({
                'Date': 'date',
                'Open': 'open',
                'High': 'high',
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume'
            })
            
            # Remove timezone from date if present
            if 'date' in df.columns and df['date'].dtype == pl.Datetime:
                df = df.with_columns(
                    pl.col('date').dt.replace_time_zone(None).alias('date')
                )
            
            return df.select(['date', 'open', 'high', 'low', 'close', 'volume'])
            
        except Exception as e:
            print(f"❌ yfinance error for {symbol}: {e}")
            return pl.DataFrame()
    
    def _to_yahoo_format(self, symbol: str, market: str) -> str:
        if '.' in symbol:
            return symbol
            
        market_suffix = {
            'HK': '.HK',
            'US': '',
            'SG': '.SI',
            'JP': '.T',
            'AU': '.AX',
        }
        
        suffix = market_suffix.get(market, '')
        return f"{symbol}{suffix}"


class DataFetcher:
    """統一數據獲取器"""
    
    def __init__(self, source: str = "yfinance"):
        self.source = source
        self._fetcher = self._get_fetcher(source)
        
    def _get_fetcher(self, source: str) -> BaseFetcher:
        fetchers = {
            'yfinance': YFinanceFetcher,
        }
        
        fetcher_class = fetchers.get(source)
        if not fetcher_class:
            raise ValueError(f"Unknown source: {source}")
            
        return fetcher_class()
        
    def fetch(self, symbol: str, start_date: str, end_date: str) -> pl.DataFrame:
        return self._fetcher.fetch(symbol, start_date, end_date)
        
    def fetch_multiple(self, symbols: List[str], market: str,
                      start_date: str, end_date: str) -> pl.DataFrame:
        return self._fetcher.fetch_multiple(symbols, market, start_date, end_date)


# 測試
if __name__ == "__main__":
    fetcher = DataFetcher("yfinance")
    
    # 單個
    df = fetcher.fetch("AAPL", "2024-01-01", "2024-12-31")
    print("Single fetch:")
    print(df.head())
