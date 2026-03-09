import logging
import sys
import time
from dataclasses import dataclass
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
    polling_seconds: int = 60
    prediction_threshold: float = 0.01
    auto_trade: bool = False
    paper_trading: bool = True


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

        self.market_buffer = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        self.equity_peak = 0.0
        self.account_value = 100000.0
        self.position_qty = 0
        self.highest_price_since_entry = 0.0

    def start(self) -> None:
        self.logger.info(
            "Starting live loop symbol=%s auto_trade=%s paper_trading=%s",
            self.config.symbol,
            self.config.auto_trade,
            self.config.paper_trading,
        )
        self.futu_connector.connect()
        try:
            while True:
                self.run_one_cycle()
                time.sleep(self.config.polling_seconds)
        finally:
            self.futu_connector.close()

    def run_one_cycle(self) -> None:
        self._check_heartbeat()
        self._sync_broker_state()

        quote = self.futu_connector.get_latest_quote(self.config.symbol)
        self.market_buffer = pd.concat([self.market_buffer, pd.DataFrame([quote])], ignore_index=True)
        self.market_buffer["Date"] = pd.to_datetime(self.market_buffer["Date"], errors="coerce")
        self.market_buffer = (
            self.market_buffer.dropna(subset=["Date"])
            .sort_values("Date")
            .drop_duplicates(subset=["Date"], keep="last")
            .reset_index(drop=True)
        )

        featured = self.feature_generator.generate(self.market_buffer).dropna().reset_index(drop=True)
        if len(featured) <= self.model_manager.data_preparer.lookback:
            self.logger.info("Warmup: waiting for more bars (%d/%d)", len(featured), self.model_manager.data_preparer.lookback)
            return

        current_price = float(featured.iloc[-1]["Close"])
        prediction = self.model_manager.predict(featured)

        # Use broker-synced values as baseline for risk/signals.
        self.equity_peak = max(self.equity_peak, self.account_value)

        if self.risk_controller.circuit_breaker_triggered(self.equity_peak, self.account_value):
            self._notify(f"🚨 Circuit breaker hit. Equity={self.account_value:.2f}")
            return

        action = "HOLD"
        threshold_up = current_price * (1 + self.config.prediction_threshold)
        threshold_down = current_price * (1 - self.config.prediction_threshold)

        if self.position_qty > 0:
            self.highest_price_since_entry = max(self.highest_price_since_entry, current_price)
            if self.risk_controller.should_stop_out(current_price, self.highest_price_since_entry):
                self._execute("SELL", self.position_qty, current_price, reason="trailing_stop")
                action = "SELL"

        if action == "HOLD":
            if prediction > threshold_up and self.position_qty == 0:
                qty = self.risk_controller.calculate_position_size(self.account_value, current_price)
                if qty > 0:
                    self._execute("BUY", qty, current_price, reason="model_signal")
                    action = "BUY"
            elif prediction < threshold_down and self.position_qty > 0:
                self._execute("SELL", self.position_qty, current_price, reason="model_signal")
                action = "SELL"

        self._notify(
            f"💓 {datetime.utcnow().isoformat()} {self.config.symbol} "
            f"price={current_price:.4f} pred={prediction:.4f} action={action} "
            f"equity={self.account_value:.2f} position={self.position_qty}"
        )


    def _check_heartbeat(self) -> None:
        try:
            ok = self.futu_connector.heartbeat()
            if not ok:
                self.logger.warning("Broker heartbeat unhealthy; proceeding with caution using last known state")
        except Exception as exc:
            self.logger.warning("Broker heartbeat check failed: %s", exc)

    def _sync_broker_state(self) -> None:
        """Sync account value and symbol position from Futu; keep last known state on failure."""
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
            synced_qty = 0
            if not positions.empty:
                symbol_code = f"{self.futu_connector.config.market_prefix}.{self.config.symbol}"
                if "code" in positions.columns:
                    row = positions[positions["code"] == symbol_code]
                elif "stock_name" in positions.columns:
                    row = positions[positions["stock_name"] == self.config.symbol]
                else:
                    row = pd.DataFrame()

                if not row.empty:
                    qty_col = "qty" if "qty" in row.columns else "can_sell_qty" if "can_sell_qty" in row.columns else None
                    if qty_col is not None:
                        synced_qty = int(float(row.iloc[0][qty_col]))

            self.position_qty = max(0, synced_qty)
            if self.position_qty == 0:
                self.highest_price_since_entry = 0.0
            self.logger.info("Synced position qty for %s: %d", self.config.symbol, self.position_qty)
        except Exception as exc:
            self.logger.warning("Position sync failed, using last known position %d: %s", self.position_qty, exc)

    def _execute(self, side: str, qty: int, price: float, reason: str) -> None:
        self.logger.info("Execute %s qty=%d price=%.4f reason=%s", side, qty, price, reason)

        if self.config.auto_trade and not self.config.paper_trading:
            self.futu_connector.place_order(self.config.symbol, qty, side, price)
        else:
            self.logger.info("Paper trading mode: simulated %s %d %s", side, qty, self.config.symbol)

        if side == "BUY":
            self.position_qty += qty
            self.highest_price_since_entry = price
        elif side == "SELL":
            self.position_qty = max(0, self.position_qty - qty)
            if self.position_qty == 0:
                self.highest_price_since_entry = 0.0

    def _notify(self, message: str) -> None:
        self.logger.info(message)
        try:
            send_tg_msg(message)
        except Exception as exc:
            self.logger.warning("Telegram heartbeat failed: %s", exc)
