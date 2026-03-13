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
        2. Massive: Reliable secondary real-time source.
        3. Futu: ONLY for Hong Kong (HK) stocks.
        4. yfinance: Long-term main player (Backup & Base reference).
        """
        
        # Layer 1: Infoway (Real-time Focus)
        data = self.infoway.get_price(symbol)
        if data:
            return data

        # Layer 2: Massive (Usable Real-time Backup)
        if self.massive:
            try:
                massive_data = self.massive.get_quote(symbol)
                if massive_data and "price" in massive_data:
                    return {
                        "price": float(massive_data["price"]),
                        "source": "MASSIVE",
                        "timestamp": datetime.now()
                    }
            except Exception as e:
                logger.debug(f"Massive fallback skipped for {symbol}: {e}")

        # Layer 3: Futu (Restricted to HK stocks as per user request)
        if self.futu and (symbol.startswith("HK.") or symbol.startswith("HKG.")):
            try:
                futu_data = self.futu.get_market_data(symbol, "HK")
                if futu_data and "price" in futu_data:
                    return futu_data
            except Exception as e:
                logger.warning(f"Futu HK fallback failed for {symbol}: {e}")

        # Layer 4: yfinance (Long-term Main Player & Global Fallback)
        try:
            # Using yfinance as a stable reference as it supports almost all markets
            ticker = yf.Ticker(symbol.split('.')[-1] if '.' in symbol else symbol)
            # Fetching fast_info which is generally quicker than history for a single point
            price = ticker.fast_info.last_price
            if price:
                return {
                    "price": float(price),
                    "source": "YFINANCE",
                    "timestamp": datetime.now()
                }
        except Exception as e:
            logger.error(f"yfinance (Main Player) failed for {symbol}: {e}")

        return None
