from __future__ import annotations

import pandas as pd
import yfinance as yf

from .base import DataSource


class YFinanceDataSource(DataSource):
    name = "yfinance"

    def get_historical_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        interval: str = "1d",
    ) -> pd.DataFrame:
        df = yf.download(
            symbol,
            start=start_date,
            end=end_date,
            interval=interval,
            auto_adjust=False,
            progress=False,
        )

        if df is None or df.empty:
            raise ValueError(f"No data returned for {symbol} via yfinance")

        if isinstance(df.columns, pd.MultiIndex):
            df = df.droplevel(0, axis=1)

        df = df.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Adj Close": "adj_close",
                "Volume": "volume",
            }
        )

        df = self.standardize(df)
        return df