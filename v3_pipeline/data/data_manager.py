"""
Unified data manager for Kiro Quant.
Supports multiple data sources: East Money (HK), Finnhub (US), yfinance (fallback).
"""
from typing import Optional, Dict, Any, List
import time
import logging

logger = logging.getLogger(__name__)


class UnifiedDataManager:
    """
    Unified data manager that automatically selects the best data source
    based on stock symbol.
    """
    
    # Cache duration
    CACHE_TTL = 15  # seconds
    
    def __init__(self, finnhub_api_key: str = None):
        self._cache = {}
        self._cache_time = {}
        self.finnhub_api_key = finnhub_api_key or "d6rntgpr01qrri54ru2gd6rntgpr01qrri54ru30"
        
        # Import fetchers
        try:
            from .east_money_fetcher import EastMoneyFetcher
            from .finnhub_fetcher import FinnhubFetcher
            self.east_money = EastMoneyFetcher()
            self.finnhub = FinnhubFetcher(self.finnhub_api_key)
        except ImportError as e:
            logger.warning(f"Data fetchers not available: {e}")
            self.east_money = None
            self.finnhub = None
    
    def _is_hk_stock(self, symbol: str) -> bool:
        """Check if symbol is HK stock."""
        symbol = symbol.upper()
        # HK stocks: 5 digits, or .HK suffix
        if "." in symbol:
            return symbol.endswith(".HK")
        return len(symbol) == 5 and symbol.isdigit()
    
    def _is_us_stock(self, symbol: str) -> bool:
        """Check if symbol is US stock."""
        symbol = symbol.upper()
        if "." in symbol:
            return symbol.endswith(".US") or "." not in symbol
        # Common US stock patterns (letters only, 1-5 chars)
        return symbol.isalpha() and len(symbol) <= 5
    
    def _get_cache_key(self, symbol: str) -> str:
        """Generate cache key."""
        return symbol.upper().replace(".HK", "").replace(".US", "")
    
    def _is_cache_valid(self, symbol: str) -> bool:
        """Check if cached data is still valid."""
        key = self._get_cache_key(symbol)
        if key not in self._cache_time:
            return False
        return time.time() - self._cache_time[key] < self.CACHE_TTL
    
    def get_quote(self, symbol: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get quote for any stock.
        
        Auto-detects market and uses appropriate data source:
        - HK stocks: East Money
        - US stocks: Finnhub (primary), yfinance (fallback)
        
        Args:
            symbol: Stock symbol
            force_refresh: Force refresh cache
            
        Returns:
            Quote dict or None
        """
        key = self._get_cache_key(symbol)
        
        # Return cached if valid
        if not force_refresh and self._is_cache_valid(symbol):
            return self._cache.get(key)
        
        quote = None
        
        # Try based on market
        if self._is_hk_stock(symbol):
            quote = self._fetch_hk(symbol)
        elif self._is_us_stock(symbol):
            quote = self._fetch_us(symbol)
        
        # Cache result
        if quote:
            self._cache[key] = quote
            self._cache_time[key] = time.time()
            logger.info(f"Fetched {symbol} from {quote.get('source', 'unknown')}")
        
        return quote
    
    def _fetch_hk(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch HK stock via East Money."""
        if self.east_money is None:
            logger.error("East Money fetcher not available")
            return None
        
        # Extract code
        code = symbol.upper().replace(".HK", "")
        
        try:
            return self.east_money.get_quote(code)
        except Exception as e:
            logger.error(f"East Money fetch failed for {symbol}: {e}")
            return None
    
    def _fetch_us(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch US stock via Finnhub."""
        if self.finnhub is None:
            logger.error("Finnhub fetcher not available")
            return self._fetch_us_yfinance(symbol)
        
        # Extract symbol
        sym = symbol.upper().replace(".US", "")
        
        try:
            quote = self.finnhub.get_quote(sym)
            if quote:
                return quote
        except Exception as e:
            logger.error(f"Finnhub fetch failed for {symbol}: {e}")
        
        # Fallback to yfinance
        return self._fetch_us_yfinance(symbol)
    
    def _fetch_us_yfinance(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fallback to yfinance for US stocks."""
        try:
            import yfinance as yf
            sym = symbol.upper().replace(".US", "")
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="1d")
            
            if hist.empty:
                return None
            
            close = hist['Close'].iloc[-1]
            open_price = hist['Open'].iloc[-1]
            high = hist['High'].iloc[-1]
            low = hist['Low'].iloc[-1]
            volume = hist['Volume'].iloc[-1]
            
            return {
                "symbol": sym,
                "price": float(close),
                "open": float(open_price),
                "high": float(high),
                "low": float(low),
                "volume": int(volume),
                "source": "yfinance"
            }
        except Exception as e:
            logger.error(f"yfinance fetch failed for {symbol}: {e}")
            return None
    
    def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get quotes for multiple stocks."""
        results = {}
        for symbol in symbols:
            quote = self.get_quote(symbol)
            if quote:
                results[symbol] = quote
        return results
    
    def clear_cache(self):
        """Clear quote cache."""
        self._cache.clear()
        self._cache_time.clear()


# Singleton
_data_manager = None

def get_data_manager(finnhub_api_key: str = None) -> UnifiedDataManager:
    """Get singleton data manager."""
    global _data_manager
    if _data_manager is None:
        _data_manager = UnifiedDataManager(finnhub_api_key)
    return _data_manager


# Convenience functions
def get_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Get quote for any stock."""
    return get_data_manager().get_quote(symbol)

def get_hk_quote(code: str) -> Optional[Dict[str, Any]]:
    """Get HK stock quote."""
    return get_data_manager().get_quote(code)

def get_us_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Get US stock quote."""
    return get_data_manager().get_quote(symbol)


if __name__ == "__main__":
    # Test
    dm = UnifiedDataManager()
    
    print("=== HK Stocks (East Money) ===")
    for code in ["00700", "09988", "03690"]:
        q = dm.get_quote(code)
        if q:
            print(f"{code} {q.get('name', '')}: HKD {q['price']:.2f}")
    
    print("\n=== US Stocks (Finnhub) ===")
    for sym in ["AAPL", "TSLA", "NVDA"]:
        q = dm.get_quote(sym)
        if q:
            print(f"{sym}: ${q['price']:.2f} ({q.get('percent', 0):+.2f}%)")
