from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class StockFrame:
    """Multi-symbol OHLCV container with MultiIndex (symbol, Date)."""

    frame: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["symbol", "Date", "Open", "High", "Low", "Close", "Volume"]))

    def append(self, symbol: str, quote: dict) -> None:
        row = {
            "symbol": symbol,
            "Date": pd.to_datetime(quote.get("Date"), errors="coerce"),
            "Open": quote.get("Open"),
            "High": quote.get("High"),
            "Low": quote.get("Low"),
            "Close": quote.get("Close"),
            "Volume": quote.get("Volume"),
        }
        incoming = pd.DataFrame([row]).dropna(subset=["Date"])
        if incoming.empty:
            return

        self.frame = pd.concat([self.frame, incoming], ignore_index=True)
        self.frame = self.frame.dropna(subset=["Date"]).drop_duplicates(subset=["symbol", "Date"], keep="last")
        self.frame = self.frame.sort_values(["symbol", "Date"]).reset_index(drop=True)

    def symbol_view(self, symbol: str) -> pd.DataFrame:
        if self.frame.empty:
            return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

        indexed = self.frame.set_index(["symbol", "Date"]).sort_index()
        try:
            sub = indexed.xs(symbol, level="symbol").reset_index()
        except KeyError:
            return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        return sub[["Date", "Open", "High", "Low", "Close", "Volume"]]
