import logging
from datetime import datetime

try:
    import yfinance as yf
except Exception:  # optional dependency in test/minimal env
    yf = None

from infoway_client import InfowayClient
import config

logger = logging.getLogger(__name__)


class DataManager:
    def __init__(self, futu_trader=None, massive_client=None, start_infoway=True):
        api_key = (config.INFOWAY_CONFIG.get("API_KEY") or "").strip()
        self.infoway = InfowayClient(api_key, config.STOCK_LIST)
        self.futu = futu_trader
        self.massive = massive_client
        self._infoway_enabled = bool(api_key)

        if start_infoway and self._infoway_enabled:
            self.infoway.start()
        elif start_infoway:
            logger.warning("Infoway disabled due to missing API key")

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
                        "timestamp": datetime.now(),
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
        if yf is None:
            logger.warning("yfinance unavailable; unable to serve fallback data for %s", symbol)
            return None

        try:
            ticker = yf.Ticker(symbol.split(".")[-1] if "." in symbol else symbol)
            fast_info = getattr(ticker, "fast_info", None)
            price = None
            if fast_info is not None:
                price = getattr(fast_info, "last_price", None)
                if price is None and isinstance(fast_info, dict):
                    price = fast_info.get("lastPrice") or fast_info.get("last_price")

            if not price:
                history = ticker.history(period="1d", interval="1m")
                if history is not None and not history.empty:
                    price = history["Close"].dropna().iloc[-1]

            if price:
                return {
                    "price": float(price),
                    "source": "YFINANCE",
                    "timestamp": datetime.now(),
                }
        except Exception as e:
            logger.error(f"yfinance (Main Player) failed for {symbol}: {e}")

        return None
