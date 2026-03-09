import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

REQUIRED_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]


def _build_stderr_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


@dataclass
class DownloadConfig:
    storage_dir: Path = Path("v3_pipeline/data/storage")
    auto_save: bool = True
    default_format: str = "parquet"


class HistoricalDataDownloader:
    """Acquisition module for historical OHLCV data.

    Provides robust yfinance download with schema normalization:
    ['Date', 'Open', 'High', 'Low', 'Close', 'Volume'].
    """

    def __init__(self, config: Optional[DownloadConfig] = None):
        self.config = config or DownloadConfig()
        self.storage_dir = Path(self.config.storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.logger = _build_stderr_logger(self.__class__.__name__)

    def fetch_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        interval: str = "1d",
        save: Optional[bool] = None,
        file_format: Optional[str] = None,
    ) -> pd.DataFrame:
        self.logger.info(
            "Downloading %s from yfinance (%s -> %s, interval=%s)",
            symbol,
            start_date,
            end_date,
            interval,
        )
        try:
            import yfinance as yf

            raw = yf.download(
                symbol,
                start=start_date,
                end=end_date,
                interval=interval,
                progress=False,
                auto_adjust=False,
            )
        except Exception as exc:
            self.logger.exception("yfinance download failed for %s: %s", symbol, exc)
            raise

        if raw.empty:
            message = (
                f"No data returned for symbol={symbol}, interval={interval}, "
                f"range={start_date}..{end_date}"
            )
            self.logger.error(message)
            raise ValueError(message)

        normalized = self._normalize_schema(raw)
        self.logger.info("Downloaded %d rows for %s", len(normalized), symbol)

        should_save = self.config.auto_save if save is None else save
        if should_save:
            fmt = file_format or self.config.default_format
            self.save_history(normalized, symbol=symbol, file_format=fmt)

        return normalized

    def save_history(self, data: pd.DataFrame, symbol: str, file_format: str = "parquet") -> Path:
        safe_symbol = symbol.replace("/", "_").replace(" ", "_")
        file_format = file_format.lower().strip()

        if file_format not in {"parquet", "csv"}:
            raise ValueError("file_format must be one of {'parquet', 'csv'}")

        filename = f"{safe_symbol}_history.{file_format}"
        output_path = self.storage_dir / filename

        try:
            if file_format == "parquet":
                data.to_parquet(output_path, index=False)
            else:
                data.to_csv(output_path, index=False)
            self.logger.info("Saved %s rows to %s", len(data), output_path)
        except Exception as exc:
            self.logger.exception("Failed to save %s: %s", output_path, exc)
            raise

        return output_path

    def _normalize_schema(self, data: pd.DataFrame) -> pd.DataFrame:
        frame = data.copy()

        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)

        if "Date" not in frame.columns:
            frame = frame.reset_index()

        if "Adj Close" in frame.columns and "Close" not in frame.columns:
            frame = frame.rename(columns={"Adj Close": "Close"})

        missing = [col for col in ["Date", "Open", "High", "Low", "Close"] if col not in frame.columns]
        if missing:
            raise ValueError(f"Downloaded data missing required columns: {missing}")

        if "Volume" not in frame.columns:
            frame["Volume"] = 0

        normalized = frame[REQUIRED_COLUMNS].copy()
        normalized["Date"] = pd.to_datetime(normalized["Date"], errors="coerce")

        bad_dates = normalized["Date"].isna().sum()
        if bad_dates:
            self.logger.warning("Dropping %d rows with invalid Date", bad_dates)
            normalized = normalized.dropna(subset=["Date"])

        for col in ["Open", "High", "Low", "Close", "Volume"]:
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")

        before_drop = len(normalized)
        normalized = normalized.dropna(subset=["Open", "High", "Low", "Close"])
        dropped = before_drop - len(normalized)
        if dropped:
            self.logger.warning("Dropped %d rows with invalid OHLC values", dropped)

        normalized = normalized.sort_values("Date").drop_duplicates(subset=["Date"]).reset_index(drop=True)
        return normalized


if __name__ == "__main__":
    downloader = HistoricalDataDownloader()
    df = downloader.fetch_history("TSLA", "2020-01-01", "2024-01-01", interval="1d")
    print(df.tail())
