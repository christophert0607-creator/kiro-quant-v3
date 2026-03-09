import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from notifier import send_tg_msg
from v3_pipeline.core.futu_connector import FutuConnector
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator
from v3_pipeline.models.manager import ModelManager
from v3_pipeline.risk.manager import RiskController


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
class LiveConfig:
    symbol: str = "TSLA"
    symbols_list: list[str] = field(default_factory=list)
    polling_seconds: int = 60
    prediction_threshold: float = 0.01
    auto_trade: bool = False
    paper_trading: bool = True
    history_priming_days: int = 7
    history_retry_count: int = 3
    history_retry_backoff_seconds: float = 1.5


class LiveTradingLoop:
    """24/7-style live loop bridging real-time data -> features -> model -> execution."""

    def __init__(
        self,
        model_manager: ModelManager,
        risk_controller: RiskController,
        futu_connector: Optional[FutuConnector] = None,
        feature_generator: Optional[TechnicalIndicatorGenerator] = None,
        config: Optional[LiveConfig] = None,
    ) -> None:
        self.model_manager = model_manager
        self.risk_controller = risk_controller
        self.futu_connector = futu_connector or FutuConnector()
        self.feature_generator = feature_generator or TechnicalIndicatorGenerator()
        self.config = config or LiveConfig()
        self.logger = _build_stderr_logger(self.__class__.__name__)

        self.symbols = self.config.symbols_list or [self.config.symbol]
        self.market_buffers: dict[str, pd.DataFrame] = {
            symbol: pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
            for symbol in self.symbols
        }
        self.primed_symbols: set[str] = set()

        self.equity_peak = 0.0
        self.account_value = 100000.0
        self.position_qty_by_symbol: dict[str, int] = {symbol: 0 for symbol in self.symbols}
        self.highest_price_since_entry_by_symbol: dict[str, float] = {symbol: 0.0 for symbol in self.symbols}

    def start(self) -> None:
        self.logger.info(
            "Starting live loop symbols=%s auto_trade=%s paper_trading=%s",
            self.symbols,
            self.config.auto_trade,
            self.config.paper_trading,
        )
        self.futu_connector.connect()
        try:
            asyncio.run(self._run_forever())
        finally:
            self.futu_connector.close()

    async def _run_forever(self) -> None:
        while True:
            await self.run_one_cycle()
            await asyncio.sleep(self.config.polling_seconds)

    async def run_one_cycle(self) -> None:
        self._check_heartbeat()
        self._sync_broker_state()
        await asyncio.gather(*(self._run_symbol_cycle(symbol) for symbol in self.symbols))

    async def _run_symbol_cycle(self, symbol: str) -> None:
        lookback = int(self.model_manager.data_preparer.lookback)
        buffer_df = self.market_buffers[symbol]

        if len(buffer_df) < lookback and symbol not in self.primed_symbols:
            await self._prime_symbol_history(symbol)
            buffer_df = self.market_buffers[symbol]

        quote = await asyncio.to_thread(self.futu_connector.get_latest_quote, symbol)
        buffer_df = pd.concat([buffer_df, pd.DataFrame([quote])], ignore_index=True)
        buffer_df = self._normalize_market_buffer(buffer_df)
        self.market_buffers[symbol] = buffer_df

        self.logger.info("Buffer[%s] size: %d", symbol, len(buffer_df))

        if len(buffer_df) < lookback:
            self.logger.info("Warmup[%s]: waiting for more bars (%d/%d)", symbol, len(buffer_df), lookback)
            return

        raw_featured = self.feature_generator.generate(buffer_df)
        featured = raw_featured.dropna().reset_index(drop=True)

        if len(featured) <= lookback:
            self.logger.info("Warmup[%s]: indicators not ready (%d/%d)", symbol, len(featured), lookback)
            return

        current_price = float(featured.iloc[-1]["Close"])
        prediction = self.model_manager.predict(featured)

        self.equity_peak = max(self.equity_peak, self.account_value)
        if self.risk_controller.circuit_breaker_triggered(self.equity_peak, self.account_value):
            self._notify(f"🚨 Circuit breaker hit. Equity={self.account_value:.2f}")
            return

        position_qty = self.position_qty_by_symbol.get(symbol, 0)
        highest_price_since_entry = self.highest_price_since_entry_by_symbol.get(symbol, 0.0)

        action = "HOLD"
        threshold_up = current_price * (1 + self.config.prediction_threshold)
        threshold_down = current_price * (1 - self.config.prediction_threshold)

        if position_qty > 0:
            highest_price_since_entry = max(highest_price_since_entry, current_price)
            self.highest_price_since_entry_by_symbol[symbol] = highest_price_since_entry

            if self.risk_controller.should_stop_out(current_price, highest_price_since_entry):
                self._execute(symbol, "SELL", position_qty, current_price, reason="trailing_stop")
                action = "SELL"

        if action == "HOLD":
            position_qty = self.position_qty_by_symbol.get(symbol, 0)
            if prediction > threshold_up and position_qty == 0:
                qty = self.risk_controller.calculate_position_size(self.account_value, current_price)
                if qty > 0:
                    self._execute(symbol, "BUY", qty, current_price, reason="model_signal")
                    action = "BUY"
            elif prediction < threshold_down and position_qty > 0:
                self._execute(symbol, "SELL", position_qty, current_price, reason="model_signal")
                action = "SELL"

        self._notify(
            f"💓 {datetime.utcnow().isoformat()} {symbol} "
            f"price={current_price:.4f} pred={prediction:.4f} action={action} "
            f"equity={self.account_value:.2f} position={self.position_qty_by_symbol.get(symbol, 0)}"
        )

    def _normalize_market_buffer(self, df: pd.DataFrame) -> pd.DataFrame:
        normalized = df.copy()
        normalized["Date"] = pd.to_datetime(normalized["Date"], errors="coerce")
        normalized = (
            normalized.dropna(subset=["Date"])
            .sort_values("Date")
            .drop_duplicates(subset=["Date"], keep="last")
            .reset_index(drop=True)
        )
        return normalized

    async def _prime_symbol_history(self, symbol: str) -> None:
        lookback = int(self.model_manager.data_preparer.lookback)
        self.logger.info("HistoryPriming[%s]: start (need >=%d bars)", symbol, lookback)

        history_df = await self._download_history_with_retry(symbol)
        if history_df.empty:
            self.logger.warning("HistoryPriming[%s]: no history downloaded", symbol)
            return

        history_df = self._normalize_market_buffer(history_df)
        merged = pd.concat([history_df, self.market_buffers[symbol]], ignore_index=True)
        merged = self._normalize_market_buffer(merged)

        self.market_buffers[symbol] = merged
        self.primed_symbols.add(symbol)
        self.logger.info("HistoryPriming[%s]: done, buffer size=%d", symbol, len(merged))

    async def _download_history_with_retry(self, symbol: str) -> pd.DataFrame:
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.config.history_retry_count + 1):
            try:
                df = await asyncio.to_thread(self._download_history_yfinance, symbol)
                if not df.empty:
                    return df
            except Exception as exc:
                last_exc = exc
                self.logger.warning(
                    "HistoryPriming[%s]: yfinance attempt %d/%d failed (%s): %s",
                    symbol,
                    attempt,
                    self.config.history_retry_count,
                    exc.__class__.__name__,
                    exc,
                )

            if attempt < self.config.history_retry_count:
                await asyncio.sleep(self.config.history_retry_backoff_seconds * attempt)

        if last_exc is not None:
            self.logger.warning("HistoryPriming[%s]: exhausted retries, last error=%s", symbol, last_exc)
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    def _download_history_yfinance(self, symbol: str) -> pd.DataFrame:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=f"{self.config.history_priming_days}d", interval="1m")
        if hist is None or hist.empty:
            return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

        hist = hist.reset_index()
        date_col = "Datetime" if "Datetime" in hist.columns else "Date"
        hist = hist.rename(columns={date_col: "Date"})

        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col not in hist.columns:
                hist[col] = 0.0

        return hist[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()

    def _check_heartbeat(self) -> None:
        try:
            ok = self.futu_connector.heartbeat()
            if not ok:
                self.logger.warning("Broker heartbeat unhealthy; proceeding with caution using last known state")
        except Exception as exc:
            self.logger.warning("Broker heartbeat check failed: %s", exc)

    def _sync_broker_state(self) -> None:
        """Sync account value and symbol positions from Futu; keep last known state on failure."""
        try:
            assets = self.futu_connector.get_sync_assets()
            synced_value = float(assets.get("total_assets", self.account_value))
            if synced_value > 0:
                self.account_value = synced_value
                self.logger.info("Synced account value: %.2f", self.account_value)
        except Exception as exc:
            self.logger.warning("Asset sync failed, using last known account value %.2f: %s", self.account_value, exc)

        try:
            positions = self.futu_connector.get_sync_positions()
            for symbol in self.symbols:
                synced_qty = 0
                if not positions.empty:
                    symbol_code = f"{self.futu_connector.config.market_prefix}.{symbol}"
                    if "code" in positions.columns:
                        row = positions[positions["code"] == symbol_code]
                    elif "stock_name" in positions.columns:
                        row = positions[positions["stock_name"] == symbol]
                    else:
                        row = pd.DataFrame()

                    if not row.empty:
                        qty_col = "qty" if "qty" in row.columns else "can_sell_qty" if "can_sell_qty" in row.columns else None
                        if qty_col is not None:
                            synced_qty = int(float(row.iloc[0][qty_col]))

                self.position_qty_by_symbol[symbol] = max(0, synced_qty)
                if self.position_qty_by_symbol[symbol] == 0:
                    self.highest_price_since_entry_by_symbol[symbol] = 0.0
                self.logger.info("Synced position qty for %s: %d", symbol, self.position_qty_by_symbol[symbol])
        except Exception as exc:
            self.logger.warning("Position sync failed: %s", exc)

    def _execute(self, symbol: str, side: str, qty: int, price: float, reason: str) -> None:
        self.logger.info("Execute %s %s qty=%d price=%.4f reason=%s", symbol, side, qty, price, reason)

        if side == "BUY":
            self.position_qty_by_symbol[symbol] = self.position_qty_by_symbol.get(symbol, 0) + qty
            self.highest_price_since_entry_by_symbol[symbol] = price
        elif side == "SELL":
            self.position_qty_by_symbol[symbol] = max(0, self.position_qty_by_symbol.get(symbol, 0) - qty)
            if self.position_qty_by_symbol[symbol] == 0:
                self.highest_price_since_entry_by_symbol[symbol] = 0.0

        if self.config.auto_trade and not self.config.paper_trading:
            self.futu_connector.place_order(symbol, qty, side, price)
        else:
            self.logger.info("Paper trading mode: simulated %s %d %s", side, qty, symbol)

    def _notify(self, message: str) -> None:
        self.logger.info(message)
        try:
            send_tg_msg(message)
        except Exception as exc:
            self.logger.warning("Telegram heartbeat failed: %s", exc)
