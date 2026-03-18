"""
DuckDB Cache Layer for 8×8×139 Backtest Engine

原則: 向 API 拉一次 -> 存入本地 DuckDB -> 引擎只讀本地
"""

import duckdb
import pandas as pd
import os
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime


class DataCache:
    """本地數據緩存層 - 用 DuckDB + Parquet"""
    
    def __init__(self, cache_dir: str = "./data_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.cache_dir / "market_data.duckdb"
        self.conn = duckdb.connect(str(self.db_path))
        self._init_schema()
        
    def _init_schema(self):
        """初始化數據庫 schema"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                symbol VARCHAR,
                market VARCHAR,
                date DATE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, market, date)
            )
        """)
        
        # 建立索引
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_symbol ON ohlcv(symbol)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_date ON ohlcv(date)
        """)
        
    def fetch_and_cache(self, symbols: List[str], market: str, 
                       start_date: str, end_date: str, 
                       data_source: str = "yfinance") -> pd.DataFrame:
        """
        向 API 拉數據，存入本地緩存
        
        Args:
            symbols: 股票代碼列表
            market: 市場 (HK, US, A股, etc.)
            start_date: 開始日期
            end_date: 結束日期
            data_source: 數據源 (yfinance, futu, openbb)
        """
        from .fetcher import DataFetcher
        
        fetcher = DataFetcher(data_source)
        
        # 向 API 拉一次
        df = fetcher.fetch_multiple(symbols, market, start_date, end_date)
        
        if df.empty:
            return df
            
        # 存入 DuckDB
        self._store_ohlcv(df, market)
        
        print(f"✅ Cached {len(df)} rows for {market}")
        return df
    
    def _store_ohlcv(self, df: pd.DataFrame, market: str):
        """存入數據庫"""
        # 確保有必要的 columns
        required = ['symbol', 'date', 'open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required):
            raise ValueError(f"Missing required columns: {required}")
            
        # 添加 market
        df['market'] = market
        
        # 轉換日期格式
        df['date'] = pd.to_datetime(df['date']).dt.date
        
        # INSERT OR REPLACE
        self.conn.execute("""
            INSERT OR REPLACE INTO ohlcv (symbol, market, date, open, high, low, close, volume)
            SELECT symbol, market, date, open, high, low, close, volume
            FROM df
        """)
        
    def get_cached_data(self, symbol: str, market: str,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None) -> pd.DataFrame:
        """
        從本地緩存讀數據
        
        Args:
            symbol: 股票代碼
            market: 市場
            start_date: 開始日期 (可選)
            end_date: 結束日期 (可選)
        """
        query = f"SELECT * FROM ohlcv WHERE symbol = '{symbol}' AND market = '{market}'"
        
        if start_date:
            query += f" AND date >= '{start_date}'"
        if end_date:
            query += f" AND date <= '{end_date}'"
            
        query += " ORDER BY date"
        
        df = self.conn.execute(query).df()
        
        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
            
        return df
    
    def get_available_symbols(self, market: str) -> List[str]:
        """獲取已緩存的 symbols"""
        result = self.conn.execute(f"""
            SELECT DISTINCT symbol FROM ohlcv WHERE market = '{market}'
        """).fetchall()
        return [r[0] for r in result]
    
    def is_data_fresh(self, symbol: str, market: str, max_age_days: int = 1) -> bool:
        """檢查數據是否夠 fresh"""
        result = self.conn.execute(f"""
            SELECT MAX(date) FROM ohlcv WHERE symbol = '{symbol}' AND market = '{market}'
        """).fetchone()
        
        if not result or not result[0]:
            return False
            
        latest_date = pd.to_datetime(result[0])
        age = datetime.now() - latest_date
        
        return age.days <= max_age_days
    
    def get_cache_stats(self) -> Dict:
        """獲取緩存統計"""
        result = self.conn.execute("""
            SELECT market, COUNT(DISTINCT symbol) as symbols, COUNT(*) as rows
            FROM ohlcv
            GROUP BY market
        """).fetchall()
        
        return {r[0]: {'symbols': r[1], 'rows': r[2]} for r in result}
    
    def close(self):
        """關閉連接"""
        self.conn.close()


# 快速測試
if __name__ == "__main__":
    cache = DataCache()
    
    # 測試獲取 stats
    stats = cache.get_cache_stats()
    print("Cache stats:", stats)
    
    cache.close()
