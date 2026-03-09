import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from notifier import send_tg_msg
from v3_pipeline.core.futu_connector import FutuConnector
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator
from v3_pipeline.features.stock_frame import StockFrame
from v3_pipeline.models.manager import ModelManager
from v3_pipeline.models.strategy_factory import StrategyFactory
from v3_pipeline.models.strategy_base import StrategyBase
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
    symbols: list[str] = field(default_factory=lambda: ["TSLA"])
    polling_seconds: int = 60
    prediction_threshold: float = 0.01
    auto_trade: bool = False
    paper_trading: bool = True
    strategy_name: str = "kiro_lstm"


@dataclass
class SymbolState:
    position_qty: int = 0
    highest_price_since_entry: float = 0.0
    warmup_count: int = 0


class LiveTradingLoop:
    """24/7-style live loop bridging real-time data -> features -> model -> execution."""

    def __init__(
        self,
        model_manager: ModelManager,
        risk_controller: RiskController,
        futu_connector: Optional[FutuConnector] = None,
        feature_generator: Optional[TechnicalIndicatorGenerator] = None,
        config: Optional[LiveConfig] = None,
        config_json_path: str = "config.json",
    ) -> None:
        self.model_manager = model_manager
        self.risk_controller = risk_controller
        self.futu_connector = futu_connector or FutuConnector()
        self.feature_generator = feature_generator or TechnicalIndicatorGenerator()
        self.config = config or self._load_runtime_config(config_json_path)
        self.logger = _build_stderr_logger(self.__class__.__name__)

        self.stock_frame = StockFrame()
        self.symbol_states: dict[str, SymbolState] = {s: SymbolState() for s in self.config.symbols}
        self.strategy: StrategyBase = StrategyFactory.build(self.config.strategy_name, model_manager=self.model_manager)

        self.equity_peak = 0.0
        self.account_value = 100000.0

    def _load_runtime_config(self, config_json_path: str) -> LiveConfig:
        cfg = LiveConfig()
        p = Path(config_json_path)
        if not p.exists():
            return cfg
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            section = data.get("v3", data) if isinstance(data, dict) else {}
            symbols = section.get("symbols_list") or section.get("symbols")
            if isinstance(symbols, list) and symbols:
                cfg.symbols = [str(s).upper() for s in symbols]
            cfg.polling_seconds = int(section.get("polling_seconds", cfg.polling_seconds))
            cfg.prediction_threshold = float(section.get("prediction_threshold", cfg.prediction_threshold))
            cfg.auto_trade = bool(section.get("auto_trade", cfg.auto_trade))
            cfg.paper_trading = bool(section.get("paper_trading", cfg.paper_trading))
            cfg.strategy_name = str(section.get("strategy", cfg.strategy_name))
        except Exception as exc:
            _build_stderr_logger(self.__class__.__name__).warning("Failed to parse %s: %s", config_json_path, exc)
        return cfg

    def start(self) -> None:
        self.logger.info(
            "Starting async live loop symbols=%s strategy=%s auto_trade=%s paper_trading=%s",
            self.config.symbols,
            self.config.strategy_name,
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
        await asyncio.gather(*(self._run_symbol_cycle(symbol) for symbol in self.config.symbols))

    async def _run_symbol_cycle(self, symbol: str) -> None:
        state = self.symbol_states.setdefault(symbol, SymbolState())
        quote = await asyncio.to_thread(self.futu_connector.get_latest_quote, symbol)
        self.stock_frame.append(symbol, quote)

        symbol_buffer = self.stock_frame.symbol_view(symbol)
        featured = self.feature_generator.generate(symbol_buffer).dropna().reset_index(drop=True)
        if len(featured) <= self.model_manager.data_preparer.lookback:
            state.warmup_count += 1
            self.logger.info(
                "Warmup[%s] count=%d bars=%d/%d",
                symbol,
                state.warmup_count,
                len(featured),
                self.model_manager.data_preparer.lookback,
            )
            return

        current_price = float(featured.iloc[-1]["Close"])
        prediction = float(await asyncio.to_thread(self.strategy.predict, featured))

        self.equity_peak = max(self.equity_peak, self.account_value)
        if self.risk_controller.circuit_breaker_triggered(self.equity_peak, self.account_value):
            self._notify(f"🚨 Circuit breaker hit. Equity={self.account_value:.2f}")
            return

        action = "HOLD"
        threshold_up = current_price * (1 + self.config.prediction_threshold)
        threshold_down = current_price * (1 - self.config.prediction_threshold)

        if state.position_qty > 0:
            state.highest_price_since_entry = max(state.highest_price_since_entry, current_price)
            if self.risk_controller.should_stop_out(current_price, state.highest_price_since_entry):
                self._execute(symbol, state, "SELL", state.position_qty, current_price, reason="trailing_stop")
                action = "SELL"

        if action == "HOLD":
            if prediction > threshold_up and state.position_qty == 0:
                qty = self.risk_controller.calculate_position_size(self.account_value, current_price)
                if qty > 0:
                    self._execute(symbol, state, "BUY", qty, current_price, reason="model_signal")
                    action = "BUY"
            elif prediction < threshold_down and state.position_qty > 0:
                self._execute(symbol, state, "SELL", state.position_qty, current_price, reason="model_signal")
                action = "SELL"

        self._notify(
            f"💓 {datetime.utcnow().isoformat()} {symbol} "
            f"price={current_price:.4f} pred={prediction:.4f} action={action} "
            f"equity={self.account_value:.2f} position={state.position_qty}"
        )

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
            for symbol, state in self.symbol_states.items():
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

                state.position_qty = max(0, synced_qty)
                if state.position_qty == 0:
                    state.highest_price_since_entry = 0.0
                self.logger.info("Synced position qty for %s: %d", symbol, state.position_qty)
        except Exception as exc:
            self.logger.warning("Position sync failed: %s", exc)

    def _execute(self, symbol: str, state: SymbolState, side: str, qty: int, price: float, reason: str) -> None:
        self.logger.info("Execute %s %s qty=%d price=%.4f reason=%s", symbol, side, qty, price, reason)

        if self.config.auto_trade and not self.config.paper_trading:
            self.futu_connector.place_order(symbol, qty, side, price, order_type="MARKET")
        else:
            self.logger.info("Paper trading mode: simulated %s %d %s", side, qty, symbol)

        if side == "BUY":
            state.position_qty += qty
            state.highest_price_since_entry = price
        elif side == "SELL":
            state.position_qty = max(0, state.position_qty - qty)
            if state.position_qty == 0:
                state.highest_price_since_entry = 0.0

    def _notify(self, message: str) -> None:
        self.logger.info(message)
        try:
            send_tg_msg(message)
        except Exception as exc:
            self.logger.warning("Telegram heartbeat failed: %s", exc)
