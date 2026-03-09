from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class PrimingConfig:
    days: int = 5
    interval: str = "1m"
    retries: int = 3
    backoff_seconds: float = 1.5


class HistoryPrimer:
    def __init__(self, logger, config: Optional[PrimingConfig] = None) -> None:
        self.logger = logger
        self.config = config or PrimingConfig()

    async def prime_symbols(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        tasks = [self.prime_symbol(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks)
        return {symbol: df for symbol, df in results}

    async def prime_symbol(self, symbol: str) -> tuple[str, pd.DataFrame]:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.config.retries + 1):
            try:
                df = await asyncio.to_thread(self._download_from_yfinance, symbol)
                if not df.empty:
                    self.logger.info("HistoryPriming[%s]: downloaded %d bars", symbol, len(df))
                    return symbol, self._normalize(df)
            except Exception as exc:
                last_exc = exc
                self.logger.warning(
                    "HistoryPriming[%s]: attempt %d/%d failed (%s): %s",
                    symbol,
                    attempt,
                    self.config.retries,
                    exc.__class__.__name__,
                    exc,
                )
            if attempt < self.config.retries:
                await asyncio.sleep(self.config.backoff_seconds * attempt)

        if last_exc is not None:
            self.logger.warning("HistoryPriming[%s]: exhausted retries: %s", symbol, last_exc)
        return symbol, pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume", "data_source"])

    def _download_from_yfinance(self, symbol: str) -> pd.DataFrame:
        import requests
        import yfinance as yf

        agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Mozilla/5.0 (X11; Linux x86_64)",
        ]
        session = requests.Session()
        session.headers.update({"User-Agent": random.choice(agents)})

        ticker = yf.Ticker(symbol, session=session)
        hist = ticker.history(period=f"{self.config.days}d", interval=self.config.interval)
        if hist is None or hist.empty:
            return pd.DataFrame()

        hist = hist.reset_index()
        dt_col = "Datetime" if "Datetime" in hist.columns else "Date"
        hist = hist.rename(columns={dt_col: "Date"})
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col not in hist.columns:
                hist[col] = 0.0
        hist["data_source"] = "YF_PRIMED"
        return hist[["Date", "Open", "High", "Low", "Close", "Volume", "data_source"]]

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        out = (
            out.dropna(subset=["Date"])
            .sort_values("Date")
            .drop_duplicates(subset=["Date"], keep="last")
            .reset_index(drop=True)
        )
        return out
