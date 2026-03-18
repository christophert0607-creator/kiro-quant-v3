from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd

from .base import DataSource
from .futu_source import FutuDataSource
from .openbb_source import OpenBBDataSource
from .yfinance_source import YFinanceDataSource


def build_source(name: str, futu_host: str = "127.0.0.1", futu_port: int = 11111) -> DataSource:
    if name == "futu":
        return FutuDataSource(host=futu_host, port=futu_port)
    if name == "yfinance":
        return YFinanceDataSource()
    if name == "openbb":
        return OpenBBDataSource()
    raise ValueError(f"Unknown data source: {name}")


def get_sources_for_market(
    market_cfg: Dict[str, Any],
    futu_host: str = "127.0.0.1",
    futu_port: int = 11111,
) -> List[Tuple[str, DataSource]]:
    names = [market_cfg.get("primary_source")] + market_cfg.get("fallback_sources", [])
    seen = set()
    sources: List[Tuple[str, DataSource]] = []
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        sources.append((name, build_source(name, futu_host=futu_host, futu_port=futu_port)))
    return sources


def fetch_with_fallback(
    market_cfg: Dict[str, Any],
    symbol: str,
    start_date: str,
    end_date: str,
    interval: str = "1d",
    futu_host: str = "127.0.0.1",
    futu_port: int = 11111,
) -> pd.DataFrame:
    errors: List[str] = []
    for name, source in get_sources_for_market(market_cfg, futu_host=futu_host, futu_port=futu_port):
        try:
            df = source.get_historical_data(symbol, start_date, end_date, interval)
            quality = source.check_quality(df, end_date=end_date)
            if quality.ok:
                return df
            errors.append(f"{name}: quality errors={quality.errors}")
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    raise RuntimeError(f"All data sources failed for {symbol}. " + " | ".join(errors))
