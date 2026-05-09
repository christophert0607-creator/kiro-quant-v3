from types import SimpleNamespace
import importlib.util
import sys
from datetime import datetime, timedelta

import pandas as pd

if importlib.util.find_spec("websockets") is None:
    sys.modules.setdefault("websockets", SimpleNamespace(connect=None))

import data_manager


class DummyInfowayClient:
    def __init__(self, api_key, symbols):
        self.api_key = api_key
        self.symbols = symbols
        self.started = False

    def start(self):
        self.started = True

    def get_price(self, _symbol):
        return None


class PayloadInfowayClient(DummyInfowayClient):
    def __init__(self, api_key, symbols, payload):
        super().__init__(api_key, symbols)
        self._payload = payload

    def get_price(self, _symbol):
        return self._payload


class DummyTicker:
    def __init__(self, symbol, *, fast_info, history_data, daily_history_data=None):
        self.symbol = symbol
        self.fast_info = fast_info
        self._history_data = history_data
        self._daily_history_data = daily_history_data if daily_history_data is not None else history_data

    def history(self, period="1d", interval="1m"):
        if period == "5d" and interval == "1d":
            return self._daily_history_data
        return self._history_data


class DummyHistory:
    def __init__(self, closes):
        self._closes = closes
        self.empty = len(closes) == 0

    def __getitem__(self, key):
        if key != "Close":
            raise KeyError(key)
        return DummySeries(self._closes)


class DummySeries:
    def __init__(self, values):
        self._values = values

    def dropna(self):
        return self

    @property
    def iloc(self):
        return self

    def __getitem__(self, idx):
        return self._values[idx]


class DummyMarketDb:
    def __init__(self):
        self.quotes = []
        self.frames = []

    def save_market_quote(self, symbol, payload):
        self.quotes.append((symbol, payload))

    def save_data(self, frame, symbol=None):
        self.frames.append((symbol, frame.copy()))


def test_data_manager_disables_infoway_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(data_manager, "InfowayClient", DummyInfowayClient)
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": ""})

    dm = data_manager.DataManager(start_infoway=True)

    assert dm._infoway_enabled is False
    assert dm.infoway is None


def test_get_market_data_uses_fast_info_dict_last_price(monkeypatch):
    monkeypatch.setattr(data_manager, "InfowayClient", DummyInfowayClient)
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": "test"})

    ticker = DummyTicker(
        "TSLA",
        fast_info={"lastPrice": 222.5},
        history_data=DummyHistory([]),
    )
    monkeypatch.setattr(data_manager, "yf", SimpleNamespace(Ticker=lambda _symbol: ticker))

    dm = data_manager.DataManager(start_infoway=False)
    result = dm.get_market_data("US.TSLA")

    assert result is not None
    assert result["source"] == "YFINANCE"
    assert result["price"] == 222.5


def test_get_market_data_syncs_valid_payload_to_db(monkeypatch):
    monkeypatch.setattr(data_manager, "InfowayClient", DummyInfowayClient)
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": "test"})

    ticker = DummyTicker(
        "TSLA",
        fast_info={"lastPrice": 222.5},
        history_data=DummyHistory([]),
    )
    monkeypatch.setattr(data_manager, "yf", SimpleNamespace(Ticker=lambda _symbol: ticker))

    db = DummyMarketDb()
    dm = data_manager.DataManager(start_infoway=False, market_db=db)
    result = dm.get_market_data("US.TSLA")

    assert result is not None
    assert db.quotes == [("US.TSLA", result)]


def test_get_history_kline_syncs_indicator_features_to_db(monkeypatch):
    monkeypatch.setattr(data_manager, "InfowayClient", DummyInfowayClient)
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": "test"})

    dates = pd.date_range("2026-01-01", periods=80, freq="D")
    history = pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(80)],
            "High": [101.0 + i for i in range(80)],
            "Low": [99.0 + i for i in range(80)],
            "Close": [100.5 + i for i in range(80)],
            "Volume": [1000.0 + i for i in range(80)],
        },
        index=dates,
    )

    class KlineTicker:
        def history(self, **_kwargs):
            return history

    monkeypatch.setattr(data_manager, "yf", SimpleNamespace(Ticker=lambda _symbol: KlineTicker()))

    db = DummyMarketDb()
    dm = data_manager.DataManager(start_infoway=False, market_db=db)
    result = dm.get_history_kline("US.TSLA", count=60)

    assert len(result) == 60
    assert len(db.frames) == 1
    symbol, saved = db.frames[0]
    assert symbol == "US.TSLA"
    for column in ["RSI_14", "MACD", "MACD_SIGNAL", "MACD_HIST", "BB_UPPER", "BB_MIDDLE", "BB_LOWER"]:
        assert column in saved.columns


def test_get_market_data_falls_back_to_history_when_fast_info_missing(monkeypatch):
    monkeypatch.setattr(data_manager, "InfowayClient", DummyInfowayClient)
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": "test"})

    ticker = DummyTicker(
        "TSLA",
        fast_info=SimpleNamespace(last_price=None),
        history_data=DummyHistory([219.1, 220.3]),
    )
    monkeypatch.setattr(data_manager, "yf", SimpleNamespace(Ticker=lambda _symbol: ticker))

    dm = data_manager.DataManager(start_infoway=False)
    result = dm.get_market_data("US.TSLA")

    assert result is not None
    assert result["source"] == "YFINANCE"
    assert result["price"] == 220.3


def test_get_market_data_returns_none_when_yfinance_unavailable(monkeypatch):
    monkeypatch.setattr(data_manager, "InfowayClient", DummyInfowayClient)
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": "test"})
    monkeypatch.setattr(data_manager, "yf", None)
    monkeypatch.setattr(data_manager, "_get_east_money", None)
    monkeypatch.setattr(data_manager, "_get_east_money", None)

    dm = data_manager.DataManager(start_infoway=False)
    assert dm.get_market_data("US.TSLA") is None


def test_get_market_data_uses_daily_history_when_intraday_is_empty(monkeypatch):
    monkeypatch.setattr(data_manager, "InfowayClient", DummyInfowayClient)
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": "test"})

    ticker = DummyTicker(
        "TSLA",
        fast_info=SimpleNamespace(last_price=None),
        history_data=DummyHistory([]),
        daily_history_data=DummyHistory([210.0, 212.8]),
    )
    monkeypatch.setattr(data_manager, "yf", SimpleNamespace(Ticker=lambda _symbol: ticker))

    dm = data_manager.DataManager(start_infoway=False)
    result = dm.get_market_data("US.TSLA")

    assert result is not None
    assert result["source"] == "YFINANCE"
    assert result["price"] == 212.8


def test_get_market_data_ignores_non_positive_fast_info_price(monkeypatch):
    monkeypatch.setattr(data_manager, "InfowayClient", DummyInfowayClient)
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": "test"})

    ticker = DummyTicker(
        "TSLA",
        fast_info={"lastPrice": 0},
        history_data=DummyHistory([221.4]),
    )
    monkeypatch.setattr(data_manager, "yf", SimpleNamespace(Ticker=lambda _symbol: ticker))

    dm = data_manager.DataManager(start_infoway=False)
    result = dm.get_market_data("US.TSLA")

    assert result is not None
    assert result["price"] == 221.4


def test_validate_market_payload_rejects_null_price(monkeypatch):
    payload = {
        "price": None,
        "source": "INFOWAY",
        "timestamp": datetime.now(),
    }
    monkeypatch.setattr(
        data_manager,
        "InfowayClient",
        lambda api_key, symbols: PayloadInfowayClient(api_key, symbols, payload),
    )
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": "test"})
    monkeypatch.setattr(data_manager, "yf", None)

    dm = data_manager.DataManager(start_infoway=False)
    assert dm.get_market_data("US.TSLA") is None


def test_validate_market_payload_rejects_negative_price(monkeypatch):
    payload = {
        "price": -1,
        "source": "INFOWAY",
        "timestamp": datetime.now(),
    }
    monkeypatch.setattr(
        data_manager,
        "InfowayClient",
        lambda api_key, symbols: PayloadInfowayClient(api_key, symbols, payload),
    )
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": "test"})
    monkeypatch.setattr(data_manager, "yf", None)

    dm = data_manager.DataManager(start_infoway=False)
    assert dm.get_market_data("US.TSLA") is None


def test_validate_market_payload_rejects_malformed_timestamp(monkeypatch):
    payload = {
        "price": 100,
        "source": "INFOWAY",
        "timestamp": "not-a-timestamp",
    }
    monkeypatch.setattr(
        data_manager,
        "InfowayClient",
        lambda api_key, symbols: PayloadInfowayClient(api_key, symbols, payload),
    )
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": "test"})
    monkeypatch.setattr(data_manager, "yf", None)

    dm = data_manager.DataManager(start_infoway=False)
    assert dm.get_market_data("US.TSLA") is None


def test_quality_metrics_increment_for_invalid_and_missing(monkeypatch):
    monkeypatch.setattr(data_manager, "InfowayClient", DummyInfowayClient)
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": "test"})
    monkeypatch.setattr(data_manager, "yf", None)
    monkeypatch.setattr(data_manager, "_get_east_money", None)

    class DummyMassiveClient:
        def __init__(self):
            self._quotes = [
                {"price": None},
                {"price": None},
            ]

        def get_quote(self, _symbol):
            return self._quotes.pop(0)

    stale_futu_timestamp = datetime.now() - timedelta(seconds=301)

    class DummyFutuClient:
        def get_market_data(self, _symbol, _market):
            return {
                "price": 100,
                "source": "FUTU",
                "timestamp": stale_futu_timestamp,
            }

    dm = data_manager.DataManager(
        futu_trader=DummyFutuClient(),
        massive_client=DummyMassiveClient(),
        start_infoway=False,
    )

    assert dm.get_market_data("HK.00700") is None
    assert dm.get_market_data("HK.00700") is None

    metrics = dm.get_quality_metrics()
    assert metrics["MASSIVE"]["total_samples"] == 2
    assert metrics["MASSIVE"]["invalid_samples"] == 2
    assert metrics["MASSIVE"]["invalid_rate"] == 1.0
    assert metrics["FUTU-HK"]["total_samples"] == 2
    assert metrics["FUTU-HK"]["invalid_samples"] == 2
    assert "YFINANCE" not in metrics
