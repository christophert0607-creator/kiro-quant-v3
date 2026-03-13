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
        # Layer 1: Infoway
        data = self.infoway.get_price(symbol)
        if data:
            return data

        # Layer 2: Futu
        if self.futu:
            try:
                # Assuming FutuTrader has a method like get_market_data
                futu_data = self.futu.get_market_data(symbol, market)
                if futu_data and "price" in futu_data:
                    return futu_data
            except Exception as e:
                logger.warning(f"Futu fallback failed for {symbol}: {e}")

        # Layer 3: Massive
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
                logger.warning(f"Massive fallback failed for {symbol}: {e}")

        # Layer 4: yfinance
        try:
            ticker = yf.Ticker(symbol)
            price = ticker.fast_info.last_price
            if price:
                return {
                    "price": float(price),
                    "source": "YFINANCE",
                    "timestamp": datetime.now()
                }
        except Exception as e:
            logger.error(f"yfinance final fallback failed for {symbol}: {e}")

        return None
