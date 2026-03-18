"""
Futu OpenAPI Data Fetcher

需要先安裝: pip install futu-api
需要先啟動 Futu OpenD
"""

import polars as pl
from typing import List, Optional
from abc import ABC, abstractmethod


class FutuFetcherBase(ABC):
    """Futu 數據獲取器基類"""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 11111):
        self.host = host
        self.port = port
        self.connected = False
        
    @abstractmethod
    def connect(self) -> bool:
        pass
    
    @abstractmethod
    def fetch(self, symbol: str, market: str, start_date: str, end_date: str) -> pl.DataFrame:
        pass
    
    @abstractmethod
    def disconnect(self):
        pass


class FutuFetcher(FutuFetcherBase):
    """Futu 數據獲取器"""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 11111):
        super().__init__(host, port)
        self.quote_ctx = None
        self.trd_ctx = None
        
    def connect(self) -> bool:
        """連接 Futu OpenD"""
        try:
            import futu as ft
            
            # 創建行情 context
            self.quote_ctx = ft.OpenQuoteContext(host=self.host, port=self.port)
            self.connected = True
            print(f"✅ Connected to Futu OpenD at {self.host}:{self.port}")
            return True
            
        except ImportError:
            print("❌ futu-api not installed: pip install futu-api")
            return False
        except Exception as e:
            print(f"❌ Failed to connect to Futu: {e}")
            return False
    
    def fetch(self, symbol: str, market: str, start_date: str, end_date: str) -> pl.DataFrame:
        """獲取歷史K線數據"""
        if not self.connected:
            if not self.connect():
                return pl.DataFrame()
                
        try:
            import futu as ft
            
            # 轉換市場代碼
            market_code = self._get_market_code(market)
            code = f"{market_code}.{symbol}"
            
            # 獲取歷史數據
            ret, data = self.quote_ctx.get_history_kline(
                code=code,
                start=start_date,
                end=end_date,
                ktype=ft.KLType.K_DAY,  # 日K
                autype=ft.AuType.QFQ    # 前復權
            )
            
            if ret != ft.RET_OK:
                print(f"❌ Failed to fetch {symbol}: {data}")
                return pl.DataFrame()
                
            # 轉換為 Polars
            df = pl.from_pandas(data)
            
            # Rename columns
            df = df.rename({
                'time_key': 'date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'volume'
            })
            
            return df.select(['date', 'open', 'high', 'low', 'close', 'volume'])
            
        except Exception as e:
            print(f"❌ Error fetching {symbol}: {e}")
            return pl.DataFrame()
    
    def fetch_multiple(self, symbols: List[str], market: str, 
                      start_date: str, end_date: str) -> pl.DataFrame:
        """批量獲取"""
        all_data = []
        
        for symbol in symbols:
            df = self.fetch(symbol, market, start_date, end_date)
            if not df.is_empty():
                df = df.with_columns(pl.lit(symbol).alias('symbol'))
                all_data.append(df)
                
        if not all_data:
            return pl.DataFrame()
            
        return pl.concat(all_data)
    
    def _get_market_code(self, market: str) -> str:
        """轉換市場代碼"""
        market_map = {
            'HK': 'HK',
            'US': 'US',
            '股': 'SH',  # A股 - 上海
            'SH': 'SH',
            'SZ': 'SZ',  # A股 - 深圳
        }
        return market_map.get(market, market)
    
    def disconnect(self):
        """斷開連接"""
        if self.quote_ctx:
            self.quote_ctx.close()
        if self.trd_ctx:
            self.trd_ctx.close()
        self.connected = False
        print("🔌 Disconnected from Futu")


class DataSourceRegistry:
    """數據源註冊表"""
    
    def __init__(self):
        self.fetchers = {}
        
    def register(self, name: str, fetcher):
        """註冊數據獲取器"""
        self.fetchers[name] = fetcher
        
    def get(self, name: str):
        """獲取數據獲取器"""
        return self.fetchers.get(name)
    
    def get_all(self):
        """獲取所有數據獲取器"""
        return self.fetchers


# 創建全局註冊表
registry = DataSourceRegistry()


def get_fetcher(source: str) -> Optional[object]:
    """獲取數據獲取器"""
    return registry.get(source)


def register_fetcher(source: str, fetcher):
    """註冊數據獲取器"""
    registry.register(source, fetcher)
