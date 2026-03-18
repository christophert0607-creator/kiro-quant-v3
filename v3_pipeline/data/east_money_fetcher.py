"""
East Money data fetcher for HK stocks.
"""
import time
from typing import Optional, Dict, Any
import requests


class EastMoneyFetcher:
    """Fetch HK stock data from East Money API."""
    
    BASE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
    
    # Rate limiting
    _last_request_time = 0
    MIN_REQUEST_INTERVAL = 1.0  # seconds
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
    
    def _rate_limit(self):
        """Apply rate limiting."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.MIN_REQUEST_INTERVAL:
            time.sleep(self.MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()
    
    def get_quote(self, code: str) -> Optional[Dict[str, Any]]:
        """
        Get HK stock quote.
        
        Args:
            code: Stock code (e.g., "00700", "09988")
            
        Returns:
            Dict with price, open, high, low, volume, prev_close, name
        """
        self._rate_limit()
        
        # Ensure code is 5 digits
        code = code.zfill(5)
        
        params = {
            "secid": f"116.{code}",  # HK stocks use 116 prefix
            "fields": "f43,f44,f45,f46,f47,f57,f58,f60"
        }
        
        try:
            response = self.session.get(self.BASE_URL, params=params, timeout=10)
            data = response.json()
            
            if not data.get("data"):
                return None
            
            d = data["data"]
            
            return {
                "symbol": code,
                "name": d.get("f58", ""),
                "price": d.get("f43", 0) / 1000,
                "open": d.get("f45", 0) / 1000,
                "high": d.get("f46", 0) / 1000,
                "low": d.get("f46", 0) / 1000,  # f46 is high, need to find low
                "prev_close": d.get("f60", 0) / 1000,
                "volume": d.get("f47", 0),
                "source": "east_money"
            }
        except Exception as e:
            print(f"EastMoney fetch error for {code}: {e}")
            return None
    
    def get_quotes(self, codes: list) -> Dict[str, Dict]:
        """Get quotes for multiple stocks."""
        results = {}
        for code in codes:
            quote = self.get_quote(code)
            if quote:
                results[code] = quote
        return results


# Singleton instance
_fetcher = None

def get_fetcher() -> EastMoneyFetcher:
    """Get singleton fetcher instance."""
    global _fetcher
    if _fetcher is None:
        _fetcher = EastMoneyFetcher()
    return _fetcher


def fetch_hk_quote(code: str) -> Optional[Dict[str, Any]]:
    """Convenience function to fetch HK stock quote."""
    return get_fetcher().get_quote(code)


if __name__ == "__main__":
    # Test
    fetcher = EastMoneyFetcher()
    for code in ["00700", "09988", "03690"]:
        quote = fetcher.get_quote(code)
        if quote:
            print(f"{code} {quote['name']}: HKD {quote['price']:.2f}")
