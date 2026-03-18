from __future__ import annotations

import pandas as pd

from .base import DataSource


class OpenBBDataSource(DataSource):
    name = "openbb"

    def get_historical_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        interval: str = "1d",
    ) -> pd.DataFrame:
        try:
            from openbb import obb  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "OpenBB is not installed. Install openbb to enable this data source."
            ) from exc

        raise NotImplementedError(
            "OpenBB integration is not wired yet. Implement obb.equity.price.historical here."
        )