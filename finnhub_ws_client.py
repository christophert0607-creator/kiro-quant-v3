#!/usr/bin/env python3
"""
Finnhub WebSocket Client for Real-time US Stock Quotes
======================================================
- Connects to wss://ws.finnhub.io
- Auto-reconnect on disconnect
- Thread-safe quote cache
- Lightweight and efficient
"""

import json
import logging
import threading
import time
from typing import Dict, Optional, Callable, Set
from datetime import datetime

import websocket

logger = logging.getLogger(__name__)

# Default symbols to subscribe
DEFAULT_US_SYMBOLS = ["AAPL", "TSLA", "NVDA", "AMD", "META"]

FINNHUB_WS_URL = "wss://ws.finnhub.io?token={api_key}"


class FinnhubWSClient:
    """
    Real-time US stock quotes via Finnhub WebSocket API.
    
    Features:
    - WebSocket connection with auto-reconnect
    - In-memory quote cache
    - Thread-safe operations
    - Message callback support
    """
    
    def __init__(
        self,
        api_key: str,
        symbols: Optional[list] = None,
        on_message: Optional[Callable[[dict], None]] = None,
        reconnect_delay: int = 5,
        reconnect_max_attempts: int = 10
    ):
        """
        Initialize Finnhub WebSocket client.
        
        Args:
            api_key: Finnhub API key
            symbols: List of symbols to subscribe (default: DEFAULT_US_SYMBOLS)
            on_message: Optional callback for new messages
            reconnect_delay: Seconds to wait before reconnecting
            reconnect_max_attempts: Max reconnect attempts before giving up
        """
        self.api_key = api_key
        self.symbols = symbols or DEFAULT_US_SYMBOLS.copy()
        self.on_message_callback = on_message
        self.reconnect_delay = reconnect_delay
        self.reconnect_max_attempts = reconnect_max_attempts
        
        # WebSocket instance
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        
        # Connection state
        self._connected = False
        self._running = False
        self._reconnect_attempts = 0
        
        # Quote cache: symbol -> quote_data
        self._quote_cache: Dict[str, dict] = {}
        self._cache_lock = threading.RLock()
        
        # Stats
        self._messages_received = 0
        self._last_message_time: Optional[datetime] = None
        
    def _on_open(self, ws):
        """Handle WebSocket connection open."""
        logger.info(f"✅ Finnhub WebSocket connected")
        self._connected = True
        self._reconnect_attempts = 0
        
        # Subscribe to all symbols
        for symbol in self.symbols:
            msg = json.dumps({"type": "subscribe", "symbol": symbol})
            ws.send(msg)
            logger.debug(f"📤 Subscribed to {symbol}")
            time.sleep(0.1)  # Small delay to avoid rate limiting
            
    def _on_message(self, ws, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            self._messages_received += 1
            self._last_message_time = datetime.now()
            
            msg_type = data.get("type")
            
            if msg_type == "trade":
                # Process trade data
                for trade in data.get("data", []):
                    self._process_trade(trade)
            elif msg_type == "ping":
                # Keep-alive ping
                pass
            elif msg_type == "error":
                logger.error(f"❌ Finnhub error: {data}")
            else:
                logger.debug(f"📨 Finnhub message: {data}")
                
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse message: {message}, error: {e}")
        except Exception as e:
            logger.error(f"❌ Error processing message: {e}")
            
    def _process_trade(self, trade: dict):
        """Process individual trade and update cache."""
        symbol = trade.get("s")
        price = trade.get("p")
        timestamp = trade.get("t")
        volume = trade.get("v")
        
        if not symbol or price is None:
            return
            
        quote = {
            "symbol": symbol,
            "price": float(price),
            "timestamp": datetime.now(),
            "source": "FINNHUB_WS",
            "volume": float(volume) if volume else None,
            "raw_timestamp_ms": timestamp
        }
        
        with self._cache_lock:
            self._quote_cache[symbol] = quote
            
        # Call user callback if provided
        if self.on_message_callback:
            try:
                self.on_message_callback(quote)
            except Exception as e:
                logger.error(f"❌ Error in message callback: {e}")
                
        logger.debug(f"💰 {symbol}: ${price}")
        
    def _on_error(self, ws, error):
        """Handle WebSocket error."""
        logger.error(f"❌ Finnhub WebSocket error: {error}")
        self._connected = False
        
    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection close."""
        logger.warning(f"🔌 Finnhub WebSocket closed: {close_status_code} - {close_msg}")
        self._connected = False
        
        # Attempt reconnect if still running
        if self._running and self._reconnect_attempts < self.reconnect_max_attempts:
            self._reconnect_attempts += 1
            logger.info(f"🔄 Reconnecting... (attempt {self._reconnect_attempts}/{self.reconnect_max_attempts})")
            time.sleep(self.reconnect_delay)
            self._connect()
        elif self._reconnect_attempts >= self.reconnect_max_attempts:
            logger.error("❌ Max reconnect attempts reached. Giving up.")
            
    def _connect(self):
        """Establish WebSocket connection."""
        url = FINNHUB_WS_URL.format(api_key=self.api_key)
        
        self._ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        
        # Run in background thread
        self._ws_thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        self._ws_thread.start()
        
    def start(self):
        """Start the WebSocket client."""
        if self._running:
            logger.warning("⚠️ Finnhub client already running")
            return
            
        self._running = True
        logger.info(f"🚀 Starting Finnhub WebSocket client for symbols: {self.symbols}")
        self._connect()
        
    def stop(self):
        """Stop the WebSocket client."""
        if not self._running:
            return
            
        logger.info("🛑 Stopping Finnhub WebSocket client")
        self._running = False
        self._connected = False
        
        if self._ws:
            self._ws.close()
            
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
            
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected
        
    def get_quote(self, symbol: str) -> Optional[dict]:
        """
        Get latest quote from cache.
        
        Args:
            symbol: Stock symbol (e.g., "AAPL")
            
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
            
    def subscribe(self, symbol: str):
        """
        Subscribe to additional symbol.
        
        Args:
            symbol: Stock symbol to subscribe
        """
        if symbol in self.symbols:
            return
            
        self.symbols.append(symbol)
        
        if self._connected and self._ws:
            msg = json.dumps({"type": "subscribe", "symbol": symbol})
            self._ws.send(msg)
            logger.info(f"📤 Subscribed to {symbol}")
            
    def unsubscribe(self, symbol: str):
        """
        Unsubscribe from a symbol.
        
        Args:
            symbol: Stock symbol to unsubscribe
        """
        if symbol not in self.symbols:
            return
            
        self.symbols.remove(symbol)
        
        if self._connected and self._ws:
            msg = json.dumps({"type": "unsubscribe", "symbol": symbol})
            self._ws.send(msg)
            logger.info(f"📤 Unsubscribed from {symbol}")
            
        with self._cache_lock:
            self._quote_cache.pop(symbol, None)
            
    def get_stats(self) -> dict:
        """Get client statistics."""
        return {
            "connected": self._connected,
            "running": self._running,
            "messages_received": self._messages_received,
            "last_message_time": self._last_message_time,
            "cached_symbols": list(self._quote_cache.keys()),
            "reconnect_attempts": self._reconnect_attempts
        }


def create_finnhub_client(
    api_key: Optional[str] = None,
    symbols: Optional[list] = None,
    **kwargs
) -> FinnhubWSClient:
    """
    Factory function to create Finnhub WebSocket client.
    
    Args:
        api_key: Finnhub API key (defaults to config)
        symbols: List of symbols (defaults to DEFAULT_US_SYMBOLS)
        **kwargs: Additional arguments for FinnhubWSClient
        
    Returns:
        FinnhubWSClient instance
    """
    if api_key is None:
        # Try to get from config or use default
        try:
            import config
            api_key = getattr(config, "FINNHUB_API_KEY", None)
        except ImportError:
            pass
            
        if api_key is None:
            api_key = "d6rntgpr01qrri54ru2gd6rntgpr01qrri54ru30"
            
    return FinnhubWSClient(api_key=api_key, symbols=symbols, **kwargs)


# Simple test
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    def on_msg(quote):
        print(f"💰 {quote['symbol']}: ${quote['price']:.2f}")
        
    client = create_finnhub_client(on_message=on_msg)
    client.start()
    
    try:
        # Run for 30 seconds for testing
        for i in range(30):
            time.sleep(1)
            if i % 10 == 0:
                stats = client.get_stats()
                print(f"\n📊 Stats: {stats}")
                print(f"📈 All quotes: {client.get_all_quotes()}")
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
        print("\n✅ Test completed")
