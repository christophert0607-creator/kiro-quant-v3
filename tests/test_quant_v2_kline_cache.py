import sys
import types

import pandas as pd


fake_futu = types.SimpleNamespace(
    RET_OK=0,
    KLType=types.SimpleNamespace(K_DAY="K_DAY"),
)
sys.modules.setdefault("futu", fake_futu)

fake_xgb = types.ModuleType("xgboost")


class _FakeXGBClassifier:
    def __init__(self, *args, **kwargs):
        pass


fake_xgb.XGBClassifier = _FakeXGBClassifier
sys.modules.setdefault("xgboost", fake_xgb)

fake_sklearn = types.ModuleType("sklearn")
fake_pre = types.ModuleType("sklearn.preprocessing")
fake_model_selection = types.ModuleType("sklearn.model_selection")
fake_metrics = types.ModuleType("sklearn.metrics")

class _FakeStandardScaler:
    def fit_transform(self, x):
        return x
    def transform(self, x):
        return x

def _fake_split(X, y, test_size=0.2, shuffle=False):
    split = int(len(X) * (1 - test_size))
    return X[:split], X[split:], y[:split], y[split:]

def _fake_accuracy_score(y_true, y_pred):
    return 0.5

fake_pre.StandardScaler = _FakeStandardScaler
fake_model_selection.train_test_split = _fake_split
fake_metrics.accuracy_score = _fake_accuracy_score

sys.modules.setdefault("sklearn", fake_sklearn)
sys.modules.setdefault("sklearn.preprocessing", fake_pre)
sys.modules.setdefault("sklearn.model_selection", fake_model_selection)
sys.modules.setdefault("sklearn.metrics", fake_metrics)

fake_requests = types.ModuleType("requests")
sys.modules.setdefault("requests", fake_requests)

import quant_v2 as qv2


class _QuoteCtx:
    def __init__(self):
        self.calls = 0

    def request_history_kline(self, code, ktype=None, max_count=None):
        self.calls += 1
        data = pd.DataFrame(
            {
                "time_key": ["2026-01-01", "2026-01-02"],
                "open": [1.0, 2.0],
                "high": [1.2, 2.2],
                "low": [0.9, 1.9],
                "close": [1.1, 2.1],
                "volume": [100, 200],
            }
        )
        return qv2.ft.RET_OK, data, ""


class _Massive:
    def get_kline(self, *args, **kwargs):
        raise AssertionError("massive fallback should not be called when cache is valid")


def test_fetch_kline_uses_cache_within_ttl(monkeypatch):
    monkeypatch.setattr(qv2, "KLINE_CACHE_TTL_SECONDS", 60)

    engine = qv2.QuantV2()
    engine.quote_ctx = _QuoteCtx()
    engine.massive = _Massive()

    first = engine.fetch_kline("US.TSLA")
    second = engine.fetch_kline("US.TSLA")

    assert not first.empty
    assert not second.empty
    assert engine.quote_ctx.calls == 1


def test_fetch_kline_refreshes_cache_after_ttl(monkeypatch):
    monkeypatch.setattr(qv2, "KLINE_CACHE_TTL_SECONDS", 1)

    clock = {"value": 1000.0}

    def _fake_time():
        return clock["value"]

    monkeypatch.setattr(qv2.time, "time", _fake_time)

    engine = qv2.QuantV2()
    engine.quote_ctx = _QuoteCtx()
    engine.massive = _Massive()

    engine.fetch_kline("US.TSLA")
    clock["value"] += 2
    engine.fetch_kline("US.TSLA")

    assert engine.quote_ctx.calls == 2
