import logging
import math
from copy import deepcopy
from datetime import datetime

try:
    import yfinance as yf
except Exception:  # optional dependency in test/minimal env
    yf = None

from infoway_client import InfowayClient
import config

logger = logging.getLogger(__name__)


def _normalize_price(value):
    """Return a finite positive float price, otherwise None."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


class DataManager:
    def __init__(self, futu_trader=None, massive_client=None, start_infoway=True):
        api_key = (config.INFOWAY_CONFIG.get("API_KEY") or "").strip()
        self.infoway = InfowayClient(api_key, config.STOCK_LIST) if api_key else None
        self.futu = futu_trader
        self.massive = massive_client
        self._infoway_enabled = bool(api_key)
        self._max_data_age_seconds = int(getattr(config, "MARKET_DATA_MAX_AGE_SECONDS", 300))
        self._quality_warn_threshold = float(getattr(config, "DATA_INVALID_RATE_WARN_THRESHOLD", 0.2))
        self._quality_metrics = {}

        if start_infoway and self._infoway_enabled and self.infoway:
            try:
                self.infoway.start()
                logger.info("Infoway client started")
            except Exception as e:
                logger.warning(f"Failed to start Infoway client: {e}. Will use yfinance as primary source.")
                self._infoway_enabled = False
                self.infoway = None
        elif start_infoway:
            logger.info("Infoway disabled (no API key). Using yfinance as primary data source.")

    def _parse_timestamp(self, value):
        if isinstance(value, datetime):
            return value

        if isinstance(value, str):
            parsed_value = value.replace("Z", "+00:00") if value.endswith("Z") else value
            try:
                return datetime.fromisoformat(parsed_value)
            except ValueError:
                return None

        return None

    def _record_quality_sample(self, source, *, valid, missing=False):
        bucket = self._quality_metrics.setdefault(
            source,
            {
                "total_samples": 0,
                "invalid_samples": 0,
                "missing_samples": 0,
                "missing_rate": 0.0,
                "invalid_rate": 0.0,
                "_warned": False,
            },
        )
        bucket["total_samples"] += 1
        if not valid:
            bucket["invalid_samples"] += 1
        if missing:
            bucket["missing_samples"] += 1

        total = bucket["total_samples"]
        bucket["missing_rate"] = bucket["missing_samples"] / total
        bucket["invalid_rate"] = bucket["invalid_samples"] / total

        if bucket["invalid_rate"] >= self._quality_warn_threshold and not bucket["_warned"]:
            logger.warning(
                "Data quality warning for %s: invalid_rate=%.2f (%s/%s)",
                source,
                bucket["invalid_rate"],
                bucket["invalid_samples"],
                total,
            )
            bucket["_warned"] = True
        elif bucket["invalid_rate"] < self._quality_warn_threshold:
            bucket["_warned"] = False

    def _validate_market_payload(self, payload, source):
        if not payload:
            self._record_quality_sample(source, valid=False, missing=True)
            return None

        required_keys = ("price", "source", "timestamp")
        missing_required = any(key not in payload for key in required_keys)
        if missing_required:
            self._record_quality_sample(source, valid=False, missing=True)
            return None

        price = _normalize_price(payload.get("price"))
        if price is None:
            self._record_quality_sample(source, valid=False)
            return None

        timestamp = self._parse_timestamp(payload.get("timestamp"))
        if timestamp is None:
            self._record_quality_sample(source, valid=False)
            return None

        age_seconds = (datetime.now(timestamp.tzinfo) - timestamp).total_seconds()
        if age_seconds > self._max_data_age_seconds:
            self._record_quality_sample(source, valid=False)
            return None

        volume = payload.get("volume")
        if volume is not None:
            try:
                volume = float(volume)
            except (TypeError, ValueError):
                self._record_quality_sample(source, valid=False)
                return None

            if not math.isfinite(volume) or volume < 0:
                self._record_quality_sample(source, valid=False)
                return None

        validated_payload = dict(payload)
        validated_payload["price"] = price
        validated_payload["timestamp"] = timestamp
        validated_payload["source"] = source
        if volume is not None:
            validated_payload["volume"] = volume

        self._record_quality_sample(source, valid=True)
        return validated_payload

    def get_quality_metrics(self):
        metrics = deepcopy(self._quality_metrics)
        for source in metrics:
            metrics[source].pop("_warned", None)
        return metrics

    def get_market_data(self, symbol, market="US"):
        """
        4-Tier Data Strategy (yfinance is now PRIMARY):
        1. yfinance: PRIMARY data source - reliable and always available.
        2. Infoway: Real-time active monitoring (Top 10) - optional.
        3. Massive: Reliable secondary real-time source - optional.
        4. Futu: ONLY for Hong Kong (HK) stocks - optional.
        """
        
        # Layer 1: yfinance (PRIMARY - Most Reliable)
        if yf is not None:
            try:
                ticker = yf.Ticker(symbol.split(".")[-1] if "." in symbol else symbol)
                fast_info = getattr(ticker, "fast_info", None)
                price = None
                if fast_info is not None:
                    price = _normalize_price(getattr(fast_info, "last_price", None))
                    if price is None and isinstance(fast_info, dict):
                        price = _normalize_price(fast_info.get("lastPrice") or fast_info.get("last_price"))

                if price is None:
                    history = ticker.history(period="1d", interval="1m")
                    if history is not None and not history.empty:
                        price = _normalize_price(history["Close"].dropna().iloc[-1])

                if price is None:
                    history = ticker.history(period="5d", interval="1d")
                    if history is not None and not history.empty:
                        price = _normalize_price(history["Close"].dropna().iloc[-1])

                if price is not None:
                    payload = {
                        "price": price,
                        "source": "YFINANCE",
                        "timestamp": datetime.now(),
                    }
                    validated_yfinance = self._validate_market_payload(payload, "YFINANCE")
                    if validated_yfinance:
                        return validated_yfinance

                self._record_quality_sample("YFINANCE", valid=False, missing=True)
            except Exception as e:
                logger.debug(f"yfinance primary fetch failed for {symbol}: {e}")

        # Layer 2: Infoway (Real-time Focus - Optional Enhancement)
        if self.infoway and self._infoway_enabled:
            try:
                data = self.infoway.get_price(symbol)
                if data:
                    validated_infoway = self._validate_market_payload(data, "INFOWAY")
                    if validated_infoway:
                        return validated_infoway
            except Exception as e:
                logger.debug(f"Infoway fetch failed for {symbol}: {e}")

        # Layer 2: Massive (Usable Real-time Backup)
        if self.massive:
            try:
                massive_data = self.massive.get_quote(symbol)
                if massive_data and "price" in massive_data:
                    payload = {
                        "price": massive_data.get("price"),
                        "source": "MASSIVE",
                        "timestamp": massive_data.get("timestamp", datetime.now()),
                    }
                    if "volume" in massive_data:
                        payload["volume"] = massive_data.get("volume")
                    validated_massive = self._validate_market_payload(payload, "MASSIVE")
                    if validated_massive:
                        return validated_massive
                else:
                    self._record_quality_sample("MASSIVE", valid=False, missing=True)
            except Exception as e:
                logger.debug(f"Massive fallback skipped for {symbol}: {e}")

        # Layer 3: Futu (Restricted to HK stocks as per user request)
        if self.futu and (symbol.startswith("HK.") or symbol.startswith("HKG.")):
            try:
                futu_data = self.futu.get_market_data(symbol, "HK")
                if futu_data and "price" in futu_data:
                    validated_futu = self._validate_market_payload(futu_data, "FUTU-HK")
                    if validated_futu:
                        return validated_futu
            except Exception as e:
                logger.warning(f"Futu HK fallback failed for {symbol}: {e}")

        # All data sources exhausted
        logger.warning("All data sources failed for %s", symbol)
        return None

    def get_history_kline(self, symbol, ktype="K_DAY", count=100):
        """
        Fetch historical K-line data with fallback strategy:
        1. Futu (if available and connected)
        2. yfinance (Primary backup)
        3. Massive (Secondary backup)

        Returns: pd.DataFrame with columns [time_key, open, high, low, close, volume]
        """
        import pandas as pd
        import numpy as np
        
        # Layer 1: Futu
        if self.futu and self.futu.is_connected():
            try:
                # ktype mapping: default to ft.KLType.K_DAY
                import futu as ft
                kl_map = {
                    "K_DAY": ft.KLType.K_DAY,
                    "K_WEEK": ft.KLType.K_WEEK,
                    "K_MONTH": ft.KLType.K_MONTH,
                }
                target_ktype = kl_map.get(ktype, ft.KLType.K_DAY)
                
                # Use the quote_ctx from FutuAPIClient
                if hasattr(self.futu, "quote_ctx") and self.futu.quote_ctx:
                    ret, data, msg = self.futu.quote_ctx.request_history_kline(
                        symbol, ktype=target_ktype, max_count=count
                    )
                    if ret == 0 and not data.empty:
                        df = data.sort_values("time_key")[["time_key", "open", "high", "low", "close", "volume"]]
                        logger.info(f"📥 [DataManager] Fetched {len(df)} klines for {symbol} via FUTU")
                        return df
            except Exception as e:
                logger.debug(f"Futu kline fetch failed for {symbol}: {e}")

        # Layer 2: yfinance
        if yf is not None:
            try:
                # Map K_DAY to 1d
                period_map = {"K_DAY": "1d"}
                yf_symbol = symbol.split(".")[-1] if "." in symbol else symbol
                ticker = yf.Ticker(yf_symbol)
                
                # Get historical data (adjust range based on count)
                # For K_DAY and count=100, we need roughly 150 days to be safe
                days_needed = int(count * 1.5)
                hist = ticker.history(period=f"{days_needed}d")
                
                if not hist.empty:
                    df = hist.tail(count).copy()
                    df["time_key"] = df.index.strftime('%Y-%m-%d %H:%M:%S')
                    df = df.rename(columns={
                        "Open": "open", "High": "high", "Low": "low", 
                        "Close": "close", "Volume": "volume"
                    })
                    df = df[["time_key", "open", "high", "low", "close", "volume"]]
                    logger.info(f"📥 [DataManager] Fetched {len(df)} klines for {symbol} via YFINANCE")
                    return df
            except Exception as e:
                logger.debug(f"yfinance kline fetch failed for {symbol}: {e}")

        # Layer 3: Massive
        if self.massive:
            try:
                massive_symbol = symbol.split(".")[-1] if "." in symbol else symbol
                klines = self.massive.get_kline(massive_symbol, timespan="day", limit=count)
                if klines:
                    df = pd.DataFrame(klines)
                    # MassiveClient.get_kline already provides standard columns
                    df = df.sort_values("time_key")[["time_key", "open", "high", "low", "close", "volume"]]
                    logger.info(f"📥 [DataManager] Fetched {len(df)} klines for {symbol} via MASSIVE")
                    return df
            except Exception as e:
                logger.debug(f"Massive kline fetch failed for {symbol}: {e}")

        logger.error(f"❌ All kline sources failed for {symbol}")
        return pd.DataFrame()

    def stop(self):
        """Stop background threads (Infoway, etc.)"""
        if self.infoway:
            try:
                self.infoway.stop()
                logger.info("DataManager: Infoway client stopped")
            except Exception as e:
                logger.error(f"Error stopping Infoway: {e}")
