import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from notifier import send_tg_msg
from v3_pipeline.core.futu_connector import FutuConnector
from v3_pipeline.core.history_priming import HistoryPrimer, PrimingConfig
from v3_pipeline.core.strategy_factory import StrategyFactory
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator
from v3_pipeline.models.manager import DataPreparer, ModelManager
from v3_pipeline.risk.manager import RiskController


def _build_stderr_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
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
    history_priming_days: int = 5
    history_retry_count: int = 3
    history_retry_backoff_seconds: float = 1.5
    max_symbol_concurrency: int = 111
    crit_move_threshold: float = 0.035
    quote_timeout_seconds: float = 10.0


class LiveTradingLoop:
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
            s: pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume", "data_source"]) for s in self.symbols
        }
        self.position_qty_by_symbol: dict[str, int] = {s: 0 for s in self.symbols}
        self.highest_price_since_entry_by_symbol: dict[str, float] = {s: 0.0 for s in self.symbols}
        self.bars_held_by_symbol: dict[str, int] = {s: 0 for s in self.symbols}
        self.last_price_by_symbol: dict[str, float] = {}
        self.profile_timings: list[dict] = []

        self.equity_peak = 0.0
        self.account_value = 100000.0
        self.strategy_factory = StrategyFactory()
        self.history_primer = HistoryPrimer(
            self.logger,
            PrimingConfig(
                days=self.config.history_priming_days,
                retries=self.config.history_retry_count,
                backoff_seconds=self.config.history_retry_backoff_seconds,
            ),
        )
        self.data_preparers_by_symbol: dict[str, DataPreparer] = {
            s: DataPreparer(
                lookback=self.model_manager.data_preparer.lookback,
                target_col=self.model_manager.data_preparer.target_col,
            )
            for s in self.symbols
        }

    def start(self) -> None:
        self.futu_connector.connect()
        try:
            asyncio.run(self._run_forever())
        finally:
            self._archive_market_data()
            self.futu_connector.close()

    async def _run_forever(self) -> None:
        primed = await self.history_primer.prime_symbols(self.symbols)
        for symbol, df in primed.items():
            if not df.empty:
                self.market_buffers[symbol] = self._normalize_market_buffer(df)
        self.logger.info("History priming completed for %d symbols", len(self.symbols))

        while True:
            await self.run_one_cycle()
            await asyncio.sleep(self.config.polling_seconds)

    async def run_one_cycle(self) -> None:
        self._check_heartbeat()
        self._sync_broker_state()

        semaphore = asyncio.Semaphore(self.config.max_symbol_concurrency)

        async def guarded(symbol: str) -> None:
            async with semaphore:
                await self._run_symbol_cycle(symbol)

        await asyncio.gather(*(guarded(symbol) for symbol in self.symbols))

    async def _run_symbol_cycle(self, symbol: str) -> None:
        started = time.perf_counter()
        lookback = int(self.model_manager.data_preparer.lookback)

        try:
            quote = await asyncio.wait_for(
                asyncio.to_thread(self.futu_connector.get_latest_quote, symbol),
                timeout=self.config.quote_timeout_seconds,
            )
        except TimeoutError:
            self.logger.warning("TIMEOUT[%s]: quote fetch exceeded %.1fs, skipping cycle", symbol, self.config.quote_timeout_seconds)
            return
        except Exception as exc:
            self.logger.warning("QuoteFetchFail[%s]: %s", symbol, exc)
            return

        buffer_df = pd.concat([self.market_buffers[symbol], pd.DataFrame([quote])], ignore_index=True)
        buffer_df = self._normalize_market_buffer(buffer_df)
        self.market_buffers[symbol] = buffer_df

        self.logger.info("Buffer[%s] size: %d source=%s", symbol, len(buffer_df), quote.get("data_source", "UNKNOWN"))

        if len(buffer_df) < lookback:
            self.logger.info("Warmup[%s]: waiting for more bars (%d/%d)", symbol, len(buffer_df), lookback)
            return

        featured = self.feature_generator.generate(buffer_df)
        featured = featured.ffill().bfill().dropna().reset_index(drop=True)
        if len(featured) <= lookback:
            self.logger.info("Warmup[%s]: indicators not ready (%d/%d)", symbol, len(featured), lookback)
            return

        symbol_preparer = self.data_preparers_by_symbol[symbol]
        needs_refit = (not symbol_preparer.is_fitted) or (symbol_preparer.feature_columns is not None and any(c not in featured.columns for c in symbol_preparer.feature_columns))
        if needs_refit:
            try:
                symbol_preparer.fit_transform(featured)
                self.logger.info("[%s] Fitted with %d bars - OK", symbol, len(featured))
            except Exception as exc:
                self.logger.warning("[%s] Fitting failed: %s", symbol, exc)
                return

        current_price = float(featured.iloc[-1]["Close"])
        prediction = float(self.model_manager.predict(featured, data_preparer=symbol_preparer))
        confidence = min(1.0, abs(prediction - current_price) / max(current_price, 1e-9))
        vix_value = await asyncio.to_thread(self._get_vix)
        profile = self.strategy_factory.choose_profile(vix_value)

        self._detect_critical_move(symbol, current_price)
        self._run_trading_logic(symbol, current_price, prediction, confidence, profile.allow_long)

        elapsed_ms = (time.perf_counter() - started) * 1000
        self.profile_timings.append({"symbol": symbol, "elapsed_ms": elapsed_ms, "ts": datetime.now(timezone.utc).isoformat()})

    def _run_trading_logic(self, symbol: str, current_price: float, prediction: float, confidence: float, allow_long: bool) -> None:
        self.equity_peak = max(self.equity_peak, self.account_value)
        if self.risk_controller.circuit_breaker_triggered(self.equity_peak, self.account_value):
            self._notify(f"🚨 Circuit breaker hit. Equity={self.account_value:.2f}")
            return

        qty = self.position_qty_by_symbol.get(symbol, 0)
        self.bars_held_by_symbol[symbol] = self.bars_held_by_symbol.get(symbol, 0) + (1 if qty > 0 else 0)
        volatility = self.market_buffers[symbol]["Close"].pct_change().rolling(20).std().iloc[-1]
        stop_pct = self.strategy_factory.trailing_stop_by_volatility(float(volatility) if pd.notna(volatility) else 0.0, self.bars_held_by_symbol[symbol])

        if qty > 0:
            self.highest_price_since_entry_by_symbol[symbol] = max(self.highest_price_since_entry_by_symbol.get(symbol, 0.0), current_price)
            stop_price = self.highest_price_since_entry_by_symbol[symbol] * (1 - stop_pct)
            if current_price < stop_price:
                self._execute(symbol, "SELL", qty, current_price, "time_decay_vol_stop")
                return

        threshold_up = current_price * (1 + self.config.prediction_threshold)
        threshold_down = current_price * (1 - self.config.prediction_threshold)

        if allow_long and prediction > threshold_up and qty == 0:
            risk_pct = self.strategy_factory.confidence_to_risk_pct(confidence)
            alloc = self.account_value * risk_pct
            buy_qty = max(0, int(alloc / max(current_price, 1e-9)))
            if buy_qty > 0:
                self._execute(symbol, "BUY", buy_qty, current_price, f"model_signal_conf={confidence:.3f}")
        elif prediction < threshold_down and qty > 0:
            self._execute(symbol, "SELL", qty, current_price, "model_signal")

    def _normalize_market_buffer(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        if "data_source" not in out.columns:
            out["data_source"] = "UNKNOWN"
        return (
            out.dropna(subset=["Date"])
            .sort_values("Date")
            .drop_duplicates(subset=["Date"], keep="last")
            .reset_index(drop=True)
        )

    def _get_vix(self) -> float:
        try:
            quote = self.futu_connector.get_latest_quote("^VIX")
            return float(quote["Close"])
        except Exception:
            return 18.0

    def _detect_critical_move(self, symbol: str, current_price: float) -> None:
        last = self.last_price_by_symbol.get(symbol)
        self.last_price_by_symbol[symbol] = current_price
        if last is None or last <= 0:
            return
        move = abs(current_price - last) / last
        if move >= self.config.crit_move_threshold:
            self._notify(f"[CRIT] {symbol} moved {move:.2%} in one cycle")

    def _check_heartbeat(self) -> None:
        try:
            ok = self.futu_connector.heartbeat()
            if not ok:
                self.logger.warning("Broker heartbeat unhealthy")
        except Exception as exc:
            self.logger.warning("Heartbeat check failed: %s", exc)

    def _sync_broker_state(self) -> None:
        try:
            assets = self.futu_connector.get_sync_assets()
            self.account_value = float(assets.get("total_assets", self.account_value))
        except Exception as exc:
            self.logger.warning("Asset sync failed: %s", exc)

        try:
            positions = self.futu_connector.get_sync_positions()
            for symbol in self.symbols:
                qty = 0
                if not positions.empty and "code" in positions.columns:
                    code = f"{self.futu_connector.config.market_prefix}.{symbol}"
                    row = positions[positions["code"] == code]
                    if not row.empty:
                        qty = int(float(row.iloc[0].get("qty", row.iloc[0].get("can_sell_qty", 0))))
                self.position_qty_by_symbol[symbol] = max(0, qty)
        except Exception as exc:
            self.logger.warning("Position sync failed: %s", exc)

    def _execute(self, symbol: str, side: str, qty: int, price: float, reason: str) -> None:
        sim_slippage = 0.0005
        fill_price = price * (1 + sim_slippage if side == "BUY" else 1 - sim_slippage)

        if side == "BUY":
            self.position_qty_by_symbol[symbol] += qty
            self.highest_price_since_entry_by_symbol[symbol] = fill_price
        else:
            self.position_qty_by_symbol[symbol] = max(0, self.position_qty_by_symbol[symbol] - qty)
            if self.position_qty_by_symbol[symbol] == 0:
                self.highest_price_since_entry_by_symbol[symbol] = 0.0
                self.bars_held_by_symbol[symbol] = 0

        if self.config.auto_trade and not self.config.paper_trading:
            self.futu_connector.place_order(symbol, qty, side, fill_price)

        self.logger.info("EXEC %s %s qty=%d fill=%.4f reason=%s", symbol, side, qty, fill_price, reason)

    def _archive_market_data(self) -> None:
        out_dir = Path("v3_pipeline/logs/archive")
        out_dir.mkdir(parents=True, exist_ok=True)
        for symbol, df in self.market_buffers.items():
            if df.empty:
                continue
            df.to_parquet(out_dir / f"{symbol}_{datetime.utcnow().strftime('%Y%m%d')}.parquet", compression="snappy")
        if self.profile_timings:
            pd.DataFrame(self.profile_timings).to_parquet(out_dir / f"timings_{datetime.utcnow().strftime('%Y%m%d')}.parquet", compression="snappy")

    def _notify(self, message: str) -> None:
        self.logger.info(message)
        try:
            send_tg_msg(message)
        except Exception as exc:
            self.logger.warning("Telegram failed: %s", exc)
