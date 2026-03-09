import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

import pandas as pd


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
class FutuConfig:
    host: str = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port: int = 11111
    market_prefix: str = "US"
    trd_env: str = "SIMULATE"


class FutuConnector:
    """Minimal Futu OpenAPI adapter for quote polling, state sync, and order placement."""

    def __init__(self, config: Optional[FutuConfig] = None) -> None:
        self.config = config or FutuConfig()
        self.logger = _build_stderr_logger(self.__class__.__name__)
        self.quote_ctx = None
        self.trade_ctx = None
        self.ft = None

    def connect(self) -> None:
        import futu as ft

        self.ft = ft
        self.quote_ctx = ft.OpenQuoteContext(host=self.config.host, port=self.config.port)
        self.trade_ctx = ft.OpenUSTradeContext(host=self.config.host, port=self.config.port)
        self.logger.info("Connected to Futu OpenD at %s:%s (trd_env=%s)", self.config.host, self.config.port, self.config.trd_env)

    def close(self) -> None:
        if self.quote_ctx is not None:
            self.quote_ctx.close()
        if self.trade_ctx is not None:
            self.trade_ctx.close()
        self.logger.info("Closed Futu contexts")

    def _resolved_trd_env(self):
        if self.ft is None:
            raise RuntimeError("Futu SDK not loaded. Call connect() first.")
        return getattr(self.ft.TrdEnv, self.config.trd_env, self.ft.TrdEnv.SIMULATE)

    def get_latest_quote(self, symbol: str) -> dict:
        if self.quote_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")

        code = f"{self.config.market_prefix}.{symbol}"
        ret, df = self.quote_ctx.get_stock_quote([code])
        if ret != self.ft.RET_OK or df.empty:
            raise RuntimeError(f"Failed to fetch quote for {code}: {df}")

        row = df.iloc[0]
        return {
            "Date": pd.Timestamp.now(),
            "Open": float(row.get("open_price", row.get("last_price", 0.0))),
            "High": float(row.get("high_price", row.get("last_price", 0.0))),
            "Low": float(row.get("low_price", row.get("last_price", 0.0))),
            "Close": float(row.get("last_price", 0.0)),
            "Volume": float(row.get("volume", 0.0)),
        }

    def get_sync_assets(self) -> dict:
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")

        ret, data = self.trade_ctx.accinfo_query(trd_env=self._resolved_trd_env())
        if ret != self.ft.RET_OK or data is None or data.empty:
            raise RuntimeError(f"Failed to sync assets: {data}")

        row = data.iloc[0]
        assets = {
            "total_assets": float(row.get("total_assets", 0.0)),
            "cash": float(row.get("cash", 0.0)),
            "power": float(row.get("power", 0.0)),
        }
        return assets

    def get_sync_positions(self) -> pd.DataFrame:
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")

        ret, data = self.trade_ctx.position_list_query(trd_env=self._resolved_trd_env())
        if ret != self.ft.RET_OK:
            raise RuntimeError(f"Failed to sync positions: {data}")

        if data is None:
            return pd.DataFrame()
        return data.copy()

    def place_order(self, symbol: str, qty: int, side: str, price: Optional[float] = None) -> object:
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")
        if qty <= 0:
            raise ValueError("qty must be > 0")

        code = f"{self.config.market_prefix}.{symbol}"
        trd_side = self.ft.TrdSide.BUY if side.upper() == "BUY" else self.ft.TrdSide.SELL
        order_type = self.ft.OrderType.NORMAL
        order_price = 0 if price is None else float(price)
        ret, data = self.trade_ctx.place_order(
            price=order_price,
            qty=qty,
            code=code,
            trd_side=trd_side,
            order_type=order_type,
            trd_env=self._resolved_trd_env(),
        )
        if ret != self.ft.RET_OK:
            raise RuntimeError(f"Order placement failed: {data}")
        self.logger.info("Order placed: %s %d %s", side.upper(), qty, symbol)
        return data
