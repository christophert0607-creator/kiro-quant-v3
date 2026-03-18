#!/usr/bin/env python3
"""
East Money HK Stock Polling Client
==================================
- Polling-based client for HK stocks (no public WebSocket)
- 3-second polling interval
- Uses East Money's quote API
- Thread-safe quote cache
"""

import json
import logging
import threading
import time
from typing import Dict, Optional, Callable, List
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import requests

logger = logging.getLogger(__name__)

# Default HK symbols to subscribe (Tencent, Alibaba, Meituan, BYD, Ping An)
DEFAULT_HK_SYMBOLS = ["00700", "09988", "03690", "01211", "02318"]

# East Money API endpoints for HK stocks
EASTMONEY_HK_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_HK_BATCH_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"

# Market code for HK stocks in East Money
HK_MARKET_CODE = "116"


class EastMoneyHKClient:
    """
    Polling-based client for HK stock quotes via East Money API.
    
    Features:
    - 3-second polling interval (configurable)
    - Batch quote fetching for efficiency
    - Auto-retry on errors
    - Thread-safe quote cache
    """
    
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        poll_interval: float = 3.0,
        on_quote: Optional[Callable[[dict], None]] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ):
        """
        Initialize East Money HK client.
        
        Args:
            symbols: List of HK stock codes (default: DEFAULT_HK_SYMBOLS)
            poll_interval: Polling interval in seconds (default: 3.0)
            on_quote: Optional callback for new quotes
            max_retries: Max retries on API failure
            retry_delay: Delay between retries in seconds
        """
        self.symbols = symbols or DEFAULT_HK_SYMBOLS.copy()
        self.poll_interval = poll_interval
        self.on_quote_callback = on_quote
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        # Threading
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Quote cache: symbol -> quote_data
        self._quote_cache: Dict[str, dict] = {}
        self._cache_lock = threading.RLock()
        
        # State
        self._running = False
        self._last_poll_time: Optional[datetime] = None
        self._poll_count = 0
        self._error_count = 0
        
        # Session for connection pooling
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://quote.eastmoney.com/"
        })
        
    def _format_symbol(self, symbol: str) -> str:
        """Format HK symbol for East Money API."""
        # Remove leading zeros and prefix
        symbol = symbol.strip().lstrip("0")
        return symbol if symbol else "0"
        
    def _fetch_single_quote(self, symbol: str) -> Optional[dict]:
        """Fetch quote for single symbol."""
        code = self._format_symbol(symbol)
        
        params = {
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fltt": "2",
            "invt": "2",
            "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f170",
            "secid": f"{HK_MARKET_CODE}.{code}",
            "_": int(time.time() * 1000)
        }
        
        for attempt in range(self.max_retries):
            try:
                response = self._session.get(
                    EASTMONEY_HK_QUOTE_URL,
                    params=params,
                    timeout=10
                )
                response.raise_for_status()
                data = response.json()
                
                if data.get("data"):
                    return self._parse_quote_data(data["data"], symbol)
                return None
                
            except requests.RequestException as e:
                logger.warning(f"⚠️ Request failed for {symbol} (attempt {attempt+1}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except json.JSONDecodeError as e:
                logger.error(f"❌ JSON decode error for {symbol}: {e}")
                return None
                
        return None
        
    def _fetch_batch_quotes(self) -> Dict[str, dict]:
        """Fetch quotes for all symbols in batch."""
        if not self.symbols:
            return {}
            
        # Format symbols for batch request
        secids = [f"{HK_MARKET_CODE}.{self._format_symbol(s)}" for s in self.symbols]
        
        params = {
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fltt": "2",
            "invt": "2",
            "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f26,f27,f28,f29,f30,f31,f32,f33,f34,f35,f36,f37,f38,f39,f40,f41,f42,f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f57,f58,f59,f60,f61,f62,f63,f64,f65,f66,f67,f68,f69,f70,f71,f72,f73,f74,f75,f76,f77,f78,f79,f80,f81,f82,f83,f84,f85,f86,f87,f88,f89,f90,f91,f92,f93,f94,f95,f96,f97,f98,f99,f100",
            "secids": ",".join(secids),
            "_": int(time.time() * 1000)
        }
        
        for attempt in range(self.max_retries):
            try:
                response = self._session.get(
                    EASTMONEY_HK_BATCH_URL,
                    params=params,
                    timeout=10
                )
                response.raise_for_status()
                data = response.json()
                
                quotes = {}
                if data.get("data") and data["data"].get("diff"):
                    for item in data["data"]["diff"]:
                        quote = self._parse_batch_quote_data(item)
                        if quote:
                            quotes[quote["symbol"]] = quote
                            
                return quotes
                
            except requests.RequestException as e:
                logger.warning(f"⚠️ Batch request failed (attempt {attempt+1}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except json.JSONDecodeError as e:
                logger.error(f"❌ JSON decode error: {e}")
                return {}
                
        return {}
        
    def _parse_quote_data(self, data: dict, symbol: str) -> Optional[dict]:
        """Parse single quote response from East Money."""
        try:
            # Field mapping based on East Money API
            # f43: current price (multiplied by 100)
            # f44: open price
            # f45: high price
            # f46: low price
            # f47: volume
            # f48: amount
            # f57: stock code
            # f58: stock name
            # f60: previous close
            # f170: change percent
            
            price_raw = data.get("f43")
            if price_raw is None or price_raw == "-":
                return None
                
            price = self._parse_price(price_raw)
            if price is None or price <= 0:
                return None
                
            prev_close = self._parse_price(data.get("f60"))
            change_pct = self._parse_price(data.get("f170"))
            
            quote = {
                "symbol": symbol,
                "name": data.get("f58", ""),
                "price": price,
                "open": self._parse_price(data.get("f44")),
                "high": self._parse_price(data.get("f45")),
                "low": self._parse_price(data.get("f46")),
                "prev_close": prev_close,
                "volume": self._parse_volume(data.get("f47")),
                "amount": self._parse_volume(data.get("f48")),
                "change_pct": change_pct,
                "timestamp": datetime.now(),
                "source": "EASTMONEY"
            }
            
            return quote
            
        except Exception as e:
            logger.error(f"❌ Error parsing quote data for {symbol}: {e}")
            return None
            
    def _parse_batch_quote_data(self, data: dict) -> Optional[dict]:
        """Parse batch quote response item."""
        try:
            # f12: stock code
            # f13: market code
            # f14: stock name
            # f2: current price (multiplied by 100)
            # f3: change percent
            # f4: change amount
            # f5: volume
            # f6: amount
            # f17: open
            # f15: high
            # f16: low
            # f18: previous close
            
            code = data.get("f12")
            if not code:
                return None
                
            # Pad with leading zeros to 5 digits for HK stocks
            symbol = code.zfill(5)
            
            price = self._parse_price(data.get("f2"))
            if price is None or price <= 0:
                return None
                
            quote = {
                "symbol": symbol,
                "name": data.get("f14", ""),
                "price": price,
                "open": self._parse_price(data.get("f17")),
                "high": self._parse_price(data.get("f15")),
                "low": self._parse_price(data.get("f16")),
                "prev_close": self._parse_price(data.get("f18")),
                "volume": self._parse_volume(data.get("f5")),
                "amount": self._parse_volume(data.get("f6")),
                "change_pct": self._parse_price(data.get("f3")),
                "timestamp": datetime.now(),
                "source": "EASTMONEY"
            }
            
            return quote
            
        except Exception as e:
            logger.error(f"❌ Error parsing batch quote data: {e}")
            return None
            
    def _parse_price(self, value) -> Optional[float]:
        """Parse price from East Money response."""
        if value is None or value == "-" or value == "":
            return None
        try:
            # East Money returns prices multiplied by 100
            val = float(value)
            if val == 0:
                return None
            # Check if it needs scaling (East Money uses 2 decimal places * 100)
            if val > 10000:  # Likely needs scaling
                return round(val / 100, 2)
            return round(val, 2)
        except (ValueError, TypeError):
            return None
            
    def _parse_volume(self, value) -> Optional[float]:
        """Parse volume from East Money response."""
        if value is None or value == "-" or value == "":
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
            
    def _poll_loop(self):
        """Main polling loop."""
        logger.info(f"🚀 East Money HK polling started (interval: {self.poll_interval}s)")
        
        while not self._stop_event.is_set():
            try:
                start_time = time.time()
                
                # Fetch batch quotes
                quotes = self._fetch_batch_quotes()
                
                if quotes:
                    self._poll_count += 1
                    self._last_poll_time = datetime.now()
                    
                    # Update cache and trigger callbacks
                    with self._cache_lock:
                        for symbol, quote in quotes.items():
                            self._quote_cache[symbol] = quote
                            
                            # Trigger callback
                            if self.on_quote_callback:
                                try:
                                    self.on_quote_callback(quote)
                                except Exception as e:
                                    logger.error(f"❌ Error in quote callback: {e}")
                                    
                    logger.debug(f"📊 Updated {len(quotes)} HK quotes")
                else:
                    logger.warning("⚠️ No quotes received from East Money")
                    
                # Calculate sleep time to maintain interval
                elapsed = time.time() - start_time
                sleep_time = max(0, self.poll_interval - elapsed)
                
                if self._stop_event.wait(sleep_time):
                    break
                    
            except Exception as e:
                self._error_count += 1
                logger.error(f"❌ Polling error: {e}")
                if self._stop_event.wait(1):
                    break
                    
        logger.info("🛑 East Money HK polling stopped")
        
    def start(self):
        """Start the polling client."""
        if self._running:
            logger.warning("⚠️ East Money client already running")
            return
            
        self._running = True
        self._stop_event.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        
    def stop(self):
        """Stop the polling client."""
        if not self._running:
            return
            
        logger.info("🛑 Stopping East Money HK client")
        self._running = False
        self._stop_event.set()
        
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=10)
            
        self._session.close()
        
    def is_running(self) -> bool:
        """Check if client is running."""
        return self._running
        
    def get_quote(self, symbol: str) -> Optional[dict]:
        """
        Get latest quote from cache.
        
        Args:
            symbol: HK stock code (e.g., "00700")
            
        Returns:
            Quote dict or None if not available
        """
        with self._cache_lock:
            return self._quote_cache.get(symbol)
            
    def get_all_quotes(self) -> Dict[str, dict]:
        """
        Get all cached quotes.
        
        Returns:
            Dict of symbol -> quote_data
        """
        with self._cache_lock:
            return self._quote_cache.copy()
            
    def add_symbol(self, symbol: str):
        """
        Add symbol to watchlist.
        
        Args:
            symbol: HK stock code to add
        """
        if symbol not in self.symbols:
            self.symbols.append(symbol)
            logger.info(f"📈 Added {symbol} to HK watchlist")
            
    def remove_symbol(self, symbol: str):
        """
        Remove symbol from watchlist.
        
        Args:
            symbol: HK stock code to remove
        """
        if symbol in self.symbols:
            self.symbols.remove(symbol)
            with self._cache_lock:
                self._quote_cache.pop(symbol, None)
            logger.info(f"📉 Removed {symbol} from HK watchlist")
            
    def get_stats(self) -> dict:
        """Get client statistics."""
        return {
            "running": self._running,
            "poll_count": self._poll_count,
            "error_count": self._error_count,
            "last_poll_time": self._last_poll_time,
            "cached_symbols": list(self._quote_cache.keys()),
            "watchlist": self.symbols
        }


def create_eastmoney_hk_client(
    symbols: Optional[List[str]] = None,
    poll_interval: Optional[float] = None,
    **kwargs
) -> EastMoneyHKClient:
    """
    Factory function to create East Money HK client.
    
    Args:
        symbols: List of HK stock codes
        poll_interval: Polling interval in seconds
        **kwargs: Additional arguments for EastMoneyHKClient
        
    Returns:
        EastMoneyHKClient instance
    """
    if poll_interval is None:
        # Try to get from config
        try:
            import config
            poll_interval = getattr(config, "HK_POLL_INTERVAL_SECONDS", 3.0)
        except ImportError:
            poll_interval = 3.0
            
    return EastMoneyHKClient(
        symbols=symbols,
        poll_interval=poll_interval,
        **kwargs
    )


# Simple test
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    def on_quote(quote):
        print(f"💰 {quote['symbol']} ({quote.get('name', '')}): HK${quote['price']:.2f}")
        
    client = create_eastmoney_hk_client(on_quote=on_quote)
    client.start()
    
    try:
        # Run for 30 seconds for testing
        for i in range(30):
            time.sleep(1)
            if i % 10 == 0:
                stats = client.get_stats()
                print(f"\n📊 Stats: {stats}")
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
        print("\n✅ Test completed")
