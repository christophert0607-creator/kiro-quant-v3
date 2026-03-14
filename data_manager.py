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
        self.infoway = InfowayClient(api_key, config.STOCK_LIST)
        self.futu = futu_trader
        self.massive = massive_client
        self._infoway_enabled = bool(api_key)
        self._max_data_age_seconds = int(getattr(config, "MARKET_DATA_MAX_AGE_SECONDS", 300))
        self._quality_warn_threshold = float(getattr(config, "DATA_INVALID_RATE_WARN_THRESHOLD", 0.2))
        self._quality_metrics = {}

        if start_infoway and self._infoway_enabled:
            self.infoway.start()
        elif start_infoway:
            logger.warning("Infoway disabled due to missing API key")

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
        4-Tier Data Strategy:
        1. Infoway: Real-time active monitoring (Top 10).
        2. Massive: Reliable secondary real-time source.
        3. Futu: ONLY for Hong Kong (HK) stocks.
        4. yfinance: Long-term main player (Backup & Base reference).
        """

        # Layer 1: Infoway (Real-time Focus)
        data = self.infoway.get_price(symbol)
        if data:
            validated_infoway = self._validate_market_payload(data, "INFOWAY")
            if validated_infoway:
                return validated_infoway

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

        # Layer 4: yfinance (Long-term Main Player & Global Fallback)
        if yf is None:
            self._record_quality_sample("YFINANCE", valid=False, missing=True)
            logger.warning("yfinance unavailable; unable to serve fallback data for %s", symbol)
            return None

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
            logger.error(f"yfinance (Main Player) failed for {symbol}: {e}")

        return None
