import logging
import yfinance as yf
from datetime import datetime
from infoway_client import InfowayClient
import config

logger = logging.getLogger(__name__)

class DataManager:
    def __init__(self, futu_trader=None, massive_client=None):
        self.infoway = InfowayClient(config.INFOWAY_CONFIG["API_KEY"], config.STOCK_LIST)
        self.futu = futu_trader
        self.massive = massive_client
        self.infoway.start()

    def get_market_data(self, symbol, market="US"):
        """
        4-Tier Data Strategy:
        1. Infoway: Real-time active monitoring (Top 10).
        2. Futu: Broker quote fallback (HK preferred).
        3. Massive: Reliable secondary source.
        4. yfinance: Long-term main player (Backup & Base reference).
        """
        normalized_symbol = symbol.split('.')[-1] if '.' in symbol else symbol
        
        # Layer 1: Infoway (Real-time Focus)
        data = self.infoway.get_price(symbol)
        if data:
            return data

        # Layer 2: Futu
        if self.futu:
            try:
                if hasattr(self.futu, "get_market_data"):
                    futu_data = self.futu.get_market_data(symbol, market)
                    if futu_data and "price" in futu_data:
                        return futu_data
                elif hasattr(self.futu, "get_latest_quote"):
                    futu_quote = self.futu.get_latest_quote(normalized_symbol)
                    if futu_quote:
                        close = futu_quote.get("Close")
                        if close is not None:
                            return {
                                "price": float(close),
                                "source": futu_quote.get("data_source", "FUTU"),
                                "timestamp": datetime.now(),
                                "open": float(futu_quote.get("Open", close)),
                                "high": float(futu_quote.get("High", close)),
                                "low": float(futu_quote.get("Low", close)),
                                "volume": float(futu_quote.get("Volume", 0.0)),
                            }
            except Exception as e:
                logger.debug(f"Futu fallback skipped for {symbol}: {e}")

        # Layer 3: Massive
        if self.massive:
            try:
                massive_data = self.massive.get_quote(normalized_symbol)
                if massive_data and "price" in massive_data:
                    price = massive_data["price"]
                    return {
                        "price": float(price),
                        "source": "MASSIVE",
                        "timestamp": datetime.now(),
                        "open": float(massive_data.get("open", price)),
                        "high": float(massive_data.get("high", price)),
                        "low": float(massive_data.get("low", price)),
                        "volume": float(massive_data.get("volume", 0.0)),
                    }
            except Exception as e:
                logger.debug(f"Massive fallback skipped for {symbol}: {e}")

        # Layer 4: yfinance (Long-term Main Player & Global Fallback)
        try:
            # Using yfinance as a stable reference as it supports almost all markets
            ticker = yf.Ticker(normalized_symbol)
            # Fetching fast_info which is generally quicker than history for a single point
            price = ticker.fast_info.last_price
            if price:
                return {
                    "price": float(price),
                    "source": "YFINANCE",
                    "timestamp": datetime.now(),
                    "open": float(price),
                    "high": float(price),
                    "low": float(price),
                    "volume": 0.0,
                }
        except Exception as e:
            logger.error(f"yfinance (Main Player) failed for {symbol}: {e}")

        return None
