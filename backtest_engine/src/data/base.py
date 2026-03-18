from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


@dataclass
class DataQualityResult:
    ok: bool
    warnings: List[str]
    errors: List[str]


class DataSource(ABC):
    name: str = "base"

    @abstractmethod
    def get_historical_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        interval: str = "1d",
    ) -> pd.DataFrame:
        raise NotImplementedError

    def standardize(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        rename_map = {
            "adj close": "adj_close",
        }
        df = df.rename(columns=rename_map)
        if "date" in df.columns:
            df = df.set_index("date")
        df.index = pd.to_datetime(df.index)
        return df

    def check_quality(self, df: pd.DataFrame, end_date: Optional[str] = None) -> DataQualityResult:
        warnings: List[str] = []
        errors: List[str] = []

        missing_ratio = df.isna().mean().mean()
        if missing_ratio > 0.05:
            errors.append(f"Missing data ratio too high: {missing_ratio:.2%}")

        missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing_cols:
            errors.append(f"Missing required columns: {missing_cols}")

        if end_date is not None and len(df) > 0:
            last_ts = pd.to_datetime(df.index[-1]).date()
            target = pd.to_datetime(end_date).date()
            if last_ts < target:
                warnings.append(f"Stale data: last={last_ts} < end_date={target}")

        return DataQualityResult(ok=len(errors) == 0, warnings=warnings, errors=errors)