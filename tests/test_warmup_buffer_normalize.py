"""Regression test for V3 warmup 0/60 (live quote metadata + mixed tz).

Root cause (2026-10-30): `_normalize_market_buffer()` dropped only `data_source`.
Two compounding defects then killed every live bar:

1. The QuoteCache-backed live quote row carries a `_cached_at` metadata column
   (and `symbol` on the broker-offline fallback path). Those columns are
   non-numeric, so `ffill().bfill().dropna()` in the indicator stage wiped the
   ENTIRE feature frame -> `Warmup[...]: indicators not ready (0/60)`.

2. Primed history rows are tz-AWARE (e.g. Asia/Hong_Kong) while the live
   quote row is tz-NAIVE (`pd.Timestamp.now()` in `FutuConnector._normalize_quote`).
   `pd.to_datetime(..., errors="coerce")` on the mixed column coerces the
   tz-naive live row to NaT -> the newest live bar is silently dropped.

Fix: drop metadata columns (data_source/symbol/_cached_at) and normalize dates
while PRESERVING exchange-local wall clock (hk_alpha_features derives HK
session features from .dt.hour/.dt.minute, so a UTC shift would corrupt them).
"""

from __future__ import annotations

import pandas as pd
import pytest

from v3_pipeline.core.main_loop import LiveTradingLoop


def _loop() -> LiveTradingLoop:
    """Build a bare instance without running __init__ (avoids broker/network deps)."""
    return LiveTradingLoop.__new__(LiveTradingLoop)


def _primed_hk_history() -> pd.DataFrame:
    """Exchange-local (HKT) tz-aware primed history, as HistoryPrimer produces."""
    return pd.DataFrame(
        {
            "Date": pd.date_range("2026-10-29 09:30", periods=300, freq="1min", tz="Asia/Hong_Kong"),
            "Open": 10.0,
            "High": 10.5,
            "Low": 9.8,
            "Close": 10.2,
            "Volume": 1000.0,
            "data_source": "YF_PRIMED",
        }
    )


def _live_quote_row() -> dict:
    """Live quote as QuoteCache.get() returns it: tz-naive Date + _cached_at metadata."""
    return {
        "Date": pd.Timestamp.now(),  # tz-naive, matching FutuConnector._normalize_quote
        "Open": 12.42,
        "High": 12.5,
        "Low": 12.3,
        "Close": 12.42,
        "Volume": 1000.0,
        "data_source": "FUTU",
        "_cached_at": 1793330000.0,
    }


def test_normalize_market_buffer_keeps_live_row_and_drops_metadata():
    loop = _loop()
    history = _primed_hk_history()
    live = _live_quote_row()

    merged = pd.concat([history, pd.DataFrame([live])], ignore_index=True)
    out = loop._normalize_market_buffer(merged)

    # The live row must survive (it is the newest bar).
    assert len(out) == len(history) + 1
    # Metadata columns must be gone so indicator generation stays numeric.
    for col in ("data_source", "symbol", "_cached_at"):
        assert col not in out.columns
    # No NaT anywhere.
    assert out["Date"].isna().sum() == 0
    # Exchange-local wall clock must be preserved (HKT 09:30 for the first bar).
    first = out["Date"].iloc[0]
    assert first.tzinfo is not None
    assert (first.hour, first.minute) == (9, 30)


def test_normalize_market_buffer_preserves_hk_session_wall_clock():
    """HK session features are derived from local HKT hour/minute, never UTC."""
    loop = _loop()
    history = _primed_hk_history()
    live = _live_quote_row()

    merged = pd.concat([history, pd.DataFrame([live])], ignore_index=True)
    out = loop._normalize_market_buffer(merged)

    hours = {ts.hour for ts in out["Date"]}
    assert 9 in hours  # HK morning session still reads as 09:xx, not 01:xx UTC


def test_normalize_market_buffer_survives_indicator_dropna_stage():
    """End-to-end guard: the feature frame must not be wiped to 0 rows."""
    from v3_pipeline.features.indicators import TechnicalIndicatorGenerator

    loop = _loop()
    history = _primed_hk_history()
    live = _live_quote_row()

    merged = pd.concat([history, pd.DataFrame([live])], ignore_index=True)
    out = loop._normalize_market_buffer(merged)

    generator = TechnicalIndicatorGenerator()
    featured = generator.generate(out)
    cleaned = featured.ffill().bfill().dropna().reset_index(drop=True)

    assert len(cleaned) > 60, "warmup would fail: feature frame wiped to 0 rows"


def test_normalize_market_buffer_handles_broker_offline_symbol_column():
    """The broker-offline fallback path injects a `symbol` column too."""
    loop = _loop()
    history = _primed_hk_history()
    live = _live_quote_row()
    live["symbol"] = "0688.HK"

    merged = pd.concat([history, pd.DataFrame([live])], ignore_index=True)
    out = loop._normalize_market_buffer(merged)

    assert "symbol" not in out.columns
    assert len(out) == len(history) + 1


@pytest.mark.parametrize("tz", ["Asia/Hong_Kong", "America/New_York"])
def test_normalize_market_buffer_preserves_exchange_wall_clock_for_any_tz(tz):
    loop = _loop()
    history = _primed_hk_history().assign(
        Date=pd.date_range("2026-10-29 09:30", periods=300, freq="1min", tz=tz)
    )
    live = _live_quote_row()

    merged = pd.concat([history, pd.DataFrame([live])], ignore_index=True)
    out = loop._normalize_market_buffer(merged)

    assert len(out) == len(history) + 1
    assert out["Date"].iloc[0].hour == 9
    assert out["Date"].iloc[0].minute == 30
