"""
Finnhub data fetcher for US stocks.
"""
import time
from typing import Optional, Dict, Any
import requests


class FinnhubFetcher:
    """Fetch US stock data from Finnhub API."""
    
    BASE_URL = "https://finnhub.io/api/v1/quote"
    
    # Rate limiting - max 60 requests/minute
    _last_request_time = 0
    MIN_REQUEST_INTERVAL = 1.1  # seconds (slightly over 60/min)
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0"
        })
    
    def _rate_limit(self):
        """Apply rate limiting."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.MIN_REQUEST_INTERVAL:
            time.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()
    
    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get US stock quote.
        
        Args:
            symbol: Stock symbol (e.g., "AAPL", "TSLA")
            
        Returns:
            Dict with price, change, percent, high, low, open, prev_close
        """
        if not self.api_key:
            print("Finnhub API key not set")
            return None
        
        self._rate_limit()
        
        params = {
            "symbol": symbol.upper(),
            "token": self.api_key
        }
        
        try:
            response = self.session.get(self.BASE_URL, params=params, timeout=10)
            
            if response.status_code == 429:
                print(f"Finnhub rate limit hit, waiting...")
                time.sleep(5)
                return self.get_quote(symbol)  # Retry
            
            data = response.json()
            
            if "c" not in data or data["c"] is None:
                return None
            
            return {
                "symbol": symbol.upper(),
                "price": data.get("c", 0),
                "change": data.get("d", 0),
                "percent": data.get("dp", 0),
                "high": data.get("h", 0),
                "low": data.get("l", 0),
                "open": data.get("o", 0),
                "prev_close": data.get("pc", 0),
                "volume": data.get("v", 0),
                "source": "finnhub"
            }
        except Exception as e:
            print(f"Finnhub fetch error for {symbol}: {e}")
            return None
    
    def get_quotes(self, symbols: list) -> Dict[str, Dict]:
        """Get quotes for multiple stocks."""
        results = {}
        for symbol in symbols:
            quote = self.get_quote(symbol)
            if quote:
                results[symbol] = quote
        return results


# Singleton instance
_fetcher = None

def get_fetcher(api_key: str = None) -> FinnhubFetcher:
    """Get singleton fetcher instance."""
    global _fetcher
    if _fetcher is None:
        if not api_key:
            # Try to get from environment
            api_key = "d6rntgpr01qrri54ru2gd6rntgpr01qrri54ru30"  # User's key
        _fetcher = FinnhubFetcher(api_key)
    return _fetcher


def fetch_us_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Convenience function to fetch US stock quote."""
    return get_fetcher().get_quote(symbol)


if __name__ == "__main__":
    # Test
    fetcher = FinnhubFetcher("d6rntgpr01qrri54ru2gd6rntgpr01qrri54ru30")
    for symbol in ["AAPL", "TSLA", "NVDA"]:
        quote = fetcher.get_quote(symbol)
        if quote:
            print(f"{symbol}: ${quote['price']:.2f} ({quote['percent']:+.2f}%)")
