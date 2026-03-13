from types import SimpleNamespace
import sys

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


class DummyTicker:
    def __init__(self, symbol, *, fast_info, history_data):
        self.symbol = symbol
        self.fast_info = fast_info
        self._history_data = history_data

    def history(self, period="1d", interval="1m"):
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


def test_data_manager_disables_infoway_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(data_manager, "InfowayClient", DummyInfowayClient)
    monkeypatch.setattr(data_manager.config, "INFOWAY_CONFIG", {"API_KEY": ""})

    dm = data_manager.DataManager(start_infoway=True)

    assert dm._infoway_enabled is False
    assert dm.infoway.started is False


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

    dm = data_manager.DataManager(start_infoway=False)
    assert dm.get_market_data("US.TSLA") is None
