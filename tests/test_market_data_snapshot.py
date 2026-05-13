"""Acceptance tests for MarketDataSnapshot.

Acceptance criteria:
- Symbols with fresh cache entries are in the snapshot
- Symbols with stale/missing cache entries produce a per-symbol miss reason
- Partial failure: one symbol missing does not affect others
- Snapshot is read-only after construction (mutating cache does not change it)
- Cache is consulted exactly once per symbol (no duplicate provider calls)
- from_cache() handles None cache gracefully
"""

import time
import pytest

from v3_pipeline.data.market_snapshot import MarketDataSnapshot


# ── Minimal cache stub ────────────────────────────────────────────────────────


class _MockCache:
    def __init__(self, data: dict):
        self._data = data
        self.get_calls: list[str] = []

    def get(self, symbol, default=None):
        self.get_calls.append(symbol)
        return self._data.get(symbol, default)


def _fresh_quote(close: float = 100.0) -> dict:
    return {
        "Date": "2026-01-01",
        "Open": close,
        "High": close + 1,
        "Low": close - 1,
        "Close": close,
        "Volume": 10000,
        "data_source": "test",
        "_cached_at": time.time(),
    }


# ── from_cache() populates correct dicts ─────────────────────────────────────


def test_snapshot_includes_fresh_symbols():
    cache = _MockCache({"TSLA": _fresh_quote(200.0), "NVDA": _fresh_quote(500.0)})
    snap = MarketDataSnapshot.from_cache(cache, ["TSLA", "NVDA"])
    assert "TSLA" in snap
    assert "NVDA" in snap
    assert snap.get("TSLA")["Close"] == pytest.approx(200.0)


def test_snapshot_excludes_missing_symbols():
    cache = _MockCache({"TSLA": _fresh_quote()})
    snap = MarketDataSnapshot.from_cache(cache, ["TSLA", "NVDA"])
    assert "TSLA" in snap
    assert "NVDA" not in snap


def test_snapshot_miss_reason_for_absent_symbol():
    cache = _MockCache({})
    snap = MarketDataSnapshot.from_cache(cache, ["TSLA"])
    assert snap.miss_reason("TSLA") is not None
    assert isinstance(snap.miss_reason("TSLA"), str)
    assert len(snap.miss_reason("TSLA")) > 0


def test_snapshot_no_miss_reason_for_present_symbol():
    cache = _MockCache({"TSLA": _fresh_quote()})
    snap = MarketDataSnapshot.from_cache(cache, ["TSLA"])
    assert snap.miss_reason("TSLA") is None


# ── Partial failure isolation ─────────────────────────────────────────────────


def test_partial_failure_does_not_affect_other_symbols():
    cache = _MockCache({
        "TSLA": _fresh_quote(200.0),
        "NVDA": None,              # miss
        "AAPL": _fresh_quote(150.0),
    })
    snap = MarketDataSnapshot.from_cache(cache, ["TSLA", "NVDA", "AAPL"])
    assert snap.get("TSLA")["Close"] == pytest.approx(200.0)
    assert snap.get("NVDA") is None
    assert snap.get("AAPL")["Close"] == pytest.approx(150.0)
    assert snap.miss_reason("NVDA") is not None


def test_missing_symbols_lists_only_misses():
    cache = _MockCache({"TSLA": _fresh_quote()})
    snap = MarketDataSnapshot.from_cache(cache, ["TSLA", "NVDA", "AAPL"])
    missing = snap.missing_symbols()
    assert "NVDA" in missing
    assert "AAPL" in missing
    assert "TSLA" not in missing


def test_present_symbols_lists_only_hits():
    cache = _MockCache({"TSLA": _fresh_quote(), "NVDA": _fresh_quote()})
    snap = MarketDataSnapshot.from_cache(cache, ["TSLA", "NVDA", "AAPL"])
    present = snap.present_symbols()
    assert "TSLA" in present
    assert "NVDA" in present
    assert "AAPL" not in present


# ── Cache consulted exactly once per symbol ───────────────────────────────────


def test_cache_get_called_exactly_once_per_symbol():
    cache = _MockCache({"TSLA": _fresh_quote(), "NVDA": _fresh_quote()})
    MarketDataSnapshot.from_cache(cache, ["TSLA", "NVDA", "AAPL"])
    assert cache.get_calls.count("TSLA") == 1
    assert cache.get_calls.count("NVDA") == 1
    assert cache.get_calls.count("AAPL") == 1


# ── Snapshot immutability ─────────────────────────────────────────────────────


def test_snapshot_not_affected_by_cache_mutation():
    data = {"TSLA": _fresh_quote(200.0)}
    cache = _MockCache(data)
    snap = MarketDataSnapshot.from_cache(cache, ["TSLA"])
    # Mutate the cache after snapshot is built
    data["TSLA"] = _fresh_quote(999.0)
    # Snapshot must still return original value
    assert snap.get("TSLA")["Close"] == pytest.approx(200.0)


def test_snapshot_len_counts_present_symbols():
    cache = _MockCache({"TSLA": _fresh_quote(), "NVDA": _fresh_quote()})
    snap = MarketDataSnapshot.from_cache(cache, ["TSLA", "NVDA", "AAPL"])
    assert len(snap) == 2


# ── None cache ────────────────────────────────────────────────────────────────


def test_none_cache_treats_all_as_missing():
    snap = MarketDataSnapshot.from_cache(None, ["TSLA", "NVDA"])
    assert snap.get("TSLA") is None
    assert snap.get("NVDA") is None
    assert len(snap.missing_symbols()) == 2


def test_empty_symbols_list_produces_empty_snapshot():
    cache = _MockCache({"TSLA": _fresh_quote()})
    snap = MarketDataSnapshot.from_cache(cache, [])
    assert len(snap) == 0
    assert snap.missing_symbols() == []


# ── Quote with Close=0 treated as miss ───────────────────────────────────────


def test_zero_close_treated_as_miss():
    bad_quote = _fresh_quote(0.0)
    cache = _MockCache({"TSLA": bad_quote})
    snap = MarketDataSnapshot.from_cache(cache, ["TSLA"])
    assert "TSLA" not in snap
    assert snap.miss_reason("TSLA") is not None
