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
from v3_pipeline.core.alpha_engine import KiroAlphaEngine
from v3_pipeline.core.futu_connector import FutuConnector
from v3_pipeline.core.history_priming import HistoryPrimer, PrimingConfig
from v3_pipeline.core.monte_carlo import MonteCarloSimulator
from v3_pipeline.core.strategy_factory import StrategyFactory
from v3_pipeline.core.trade_simulation import PaperTradingSimulator, PnLTracker
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
    prediction_thresholds: dict[str, float] = field(default_factory=lambda: {"TSLA": 0.01, "TSLL": 0.01, "NVDA": 0.01})
    auto_trade: bool = False
    paper_trading: bool = True
    history_priming_days: int = 5
    history_retry_count: int = 3
    history_retry_backoff_seconds: float = 1.5
    max_symbol_concurrency: int = 111
    crit_move_threshold: float = 0.035
    quote_timeout_seconds: float = 10.0
    buy_cooldown_cycles: int = 3
    log_trade_decisions: bool = True
    swing_strategy_enabled: bool = True
    swing_rsi_oversold: float = 30.0
    swing_rsi_overbought: float = 70.0
    swing_sr_window: int = 20
    swing_sr_tolerance: float = 0.003
    bypass_ror_gate: bool = False
    diagnostics_verbose: bool = True
    quick_take_profit_pct: float = 0.01
    max_hold_bars: int = 5
    pattern_confidence_threshold: float = 0.65
    pattern_threshold_relaxation: float = 0.25


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
        self.entry_price_by_symbol: dict[str, float] = {s: 0.0 for s in self.symbols}
        self.bars_held_by_symbol: dict[str, int] = {s: 0 for s in self.symbols}
        self.cycles_since_buy_by_symbol: dict[str, int] = {s: 999999 for s in self.symbols}
        self.last_price_by_symbol: dict[str, float] = {}
        self.profile_timings: list[dict] = []
        self.latest_prices: dict[str, float] = {}

        self.equity_peak = 0.0
        self.account_value = 100000.0
        self.starting_equity = self.account_value
        self.strategy_factory = StrategyFactory()
        self.alpha_engine = KiroAlphaEngine()
        self.monte_carlo = MonteCarloSimulator()
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
        self.pnl_tracker = PnLTracker()
        self.paper_trading_simulator = PaperTradingSimulator(starting_cash=self.account_value)

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

        wfa_frame = self.alpha_engine.select_features(symbol, featured)

        symbol_preparer = self.data_preparers_by_symbol[symbol]
        desired_features = [c for c in wfa_frame.columns if c != "Date"]
        needs_refit = (
            not symbol_preparer.is_fitted
            or symbol_preparer.feature_columns != desired_features
        )
        if needs_refit:
            try:
                symbol_preparer.fit_transform(wfa_frame)
                self.logger.info("[%s] Fitted with %d bars - OK", symbol, len(wfa_frame))
            except Exception as exc:
                self.logger.warning("[%s] Fitting failed: %s", symbol, exc)
                return

        current_price = float(wfa_frame.iloc[-1]["Close"])
        self.latest_prices[symbol] = current_price
        prediction = float(self.model_manager.predict(wfa_frame, data_preparer=symbol_preparer))
        base_confidence = min(1.0, abs(prediction - current_price) / max(current_price, 1e-9))
        pattern_meta = self.model_manager.predict_pattern(wfa_frame, data_preparer=symbol_preparer)
        pattern_label = str(pattern_meta.get("pattern", "Unknown"))
        pattern_confidence = float(pattern_meta.get("confidence", 0.0))
        confidence = min(1.0, 0.7 * base_confidence + 0.3 * pattern_confidence)
        self.logger.info("Detected Pattern: %s, Prob=%.2f", pattern_label, pattern_confidence)
        self._append_pattern_snapshot(symbol, pattern_label, pattern_confidence, prediction, current_price)
        vix_value = await asyncio.to_thread(self._get_vix)
        profile = self.strategy_factory.choose_profile(vix_value)

        self._detect_critical_move(symbol, current_price)
        predicted_move = (prediction - current_price) / max(current_price, 1e-9)
        self.logger.info(
            "[%s] Prediction (inverse=%.4f, current=%.4f, change=%.2f%%)",
            symbol,
            prediction,
            current_price,
            predicted_move * 100,
        )
        self.check_and_trade(
            symbol,
            current_price,
            prediction,
            confidence,
            profile.allow_long,
            latest_frame=wfa_frame,
            pattern_label=pattern_label,
            pattern_confidence=pattern_confidence,
        )

        elapsed_ms = (time.perf_counter() - started) * 1000
        self.profile_timings.append({"symbol": symbol, "elapsed_ms": elapsed_ms, "ts": datetime.now(timezone.utc).isoformat()})

    def check_and_trade(
        self,
        symbol: str,
        current_price: float,
        prediction: float,
        confidence: float,
        allow_long: bool,
        latest_frame: Optional[pd.DataFrame] = None,
        pattern_label: str = "Unknown",
        pattern_confidence: float = 0.0,
    ) -> None:
        """Bridge model prediction to executable trading decisions."""
        self._run_trading_logic(
            symbol,
            current_price,
            prediction,
            confidence,
            allow_long,
            latest_frame=latest_frame,
            pattern_label=pattern_label,
            pattern_confidence=pattern_confidence,
        )

    def _run_trading_logic(
        self,
        symbol: str,
        current_price: float,
        prediction: float,
        confidence: float,
        allow_long: bool,
        latest_frame: Optional[pd.DataFrame] = None,
        pattern_label: str = "Unknown",
        pattern_confidence: float = 0.0,
    ) -> None:
        self.equity_peak = max(self.equity_peak, self.account_value)
        if self.risk_controller.circuit_breaker_triggered(self.equity_peak, self.account_value):
            self._notify(f"🚨 Circuit breaker hit. Equity={self.account_value:.2f}")
            return

        qty_raw = int(self.position_qty_by_symbol.get(symbol, 0))
        qty = max(0, qty_raw)
        if qty_raw < 0:
            self.logger.warning("DIAG_QTY[%s] invalid negative qty=%d; clamped to 0", symbol, qty_raw)
        self.bars_held_by_symbol[symbol] = self.bars_held_by_symbol.get(symbol, 0) + (1 if qty > 0 else 0)
        if qty > 0:
            self.cycles_since_buy_by_symbol[symbol] = self.cycles_since_buy_by_symbol.get(symbol, 0) + 1
        volatility = self.market_buffers[symbol]["Close"].pct_change().rolling(20).std().iloc[-1]
        stop_pct = self.strategy_factory.trailing_stop_by_volatility(float(volatility) if pd.notna(volatility) else 0.0, self.bars_held_by_symbol[symbol])

        if qty > 0:
            self.highest_price_since_entry_by_symbol[symbol] = max(self.highest_price_since_entry_by_symbol.get(symbol, 0.0), current_price)
            stop_price = self.highest_price_since_entry_by_symbol[symbol] * (1 - stop_pct)
            if current_price < stop_price:
                self._execute(symbol, "SELL", qty, current_price, "time_decay_vol_stop")
                return

            entry_price = float(self.entry_price_by_symbol.get(symbol, 0.0) or 0.0)
            if entry_price <= 0:
                entry_price = current_price
                self.entry_price_by_symbol[symbol] = entry_price

            # RISK BOUNDARY: quick take-profit immediately de-risks once a small gain is locked.
            take_profit_price = entry_price * (1 + max(0.0, float(self.config.quick_take_profit_pct)))
            if current_price >= take_profit_price:
                self._execute(symbol, "SELL", qty, current_price, f"quick_take_profit_{self.config.quick_take_profit_pct:.4f}")
                return

            # RISK BOUNDARY: time-based exit caps exposure duration and fee drag from over-holding.
            max_hold_bars = max(1, int(self.config.max_hold_bars))
            if self.bars_held_by_symbol.get(symbol, 0) >= max_hold_bars:
                self._execute(symbol, "SELL", qty, current_price, f"max_hold_{max_hold_bars}_bars")
                return

        symbol_threshold = float(self.config.prediction_thresholds.get(symbol, self.config.prediction_threshold))
        if pattern_confidence >= self.config.pattern_confidence_threshold and pattern_label in {"DoubleBottom", "UpTrend", "Reversal"}:
            symbol_threshold = symbol_threshold * max(0.5, 1.0 - self.config.pattern_threshold_relaxation)
        threshold_up = current_price * (1 + symbol_threshold)
        threshold_down = current_price * (1 - symbol_threshold)
        predicted_move = (prediction - current_price) / max(current_price, 1e-9)
        model_buy_signal = predicted_move > symbol_threshold
        model_sell_signal = predicted_move < -symbol_threshold
        swing = self._evaluate_swing_signal(symbol, current_price, latest_frame)

        if self.config.log_trade_decisions:
            self.logger.info(
                "TRADE_CHECK[%s] allow_long=%s qty=%d pred=%.4f px=%.4f move=%.2f%% up=%.4f down=%.4f conf=%.3f pattern=%s p_conf=%.2f",
                symbol,
                allow_long,
                qty,
                prediction,
                current_price,
                predicted_move * 100,
                threshold_up,
                threshold_down,
                confidence,
                pattern_label,
                pattern_confidence,
            )
            if self.config.swing_strategy_enabled:
                self.logger.info(
                    "SWING_CHECK[%s] rsi=%.2f buy=%s sell=%s support=%.4f resistance=%.4f near_support=%s near_resistance=%s",
                    symbol,
                    swing["rsi"],
                    swing["buy_signal"],
                    swing["sell_signal"],
                    swing["support"],
                    swing["resistance"],
                    swing["near_support"],
                    swing["near_resistance"],
                )
            if self.config.diagnostics_verbose:
                self.logger.info(
                    "DIAG_GATE[%s] allow_long=%s qty=%d swing_buy=%s swing_sell=%s model_buy=%s model_sell=%s bypass_ror=%s",
                    symbol,
                    allow_long,
                    qty,
                    swing["buy_signal"],
                    swing["sell_signal"],
                    model_buy_signal,
                    model_sell_signal,
                    self.config.bypass_ror_gate,
                )

        if not allow_long and model_buy_signal and qty == 0:
            self.logger.info(
                "PROFILE_GATE[%s] blocked BUY because profile disallows long exposure (pred_move=%.2f%%)",
                symbol,
                predicted_move * 100,
            )
            return

        if self.config.swing_strategy_enabled:
            if qty == 0 and allow_long and swing["buy_signal"]:
                # RISK BOUNDARY: swing entries still use existing confidence-based risk sizing.
                risk_pct = self.strategy_factory.confidence_to_risk_pct(confidence)
                alloc = self.account_value * risk_pct
                buy_qty = max(0, int(alloc / max(current_price, 1e-9)))
                if buy_qty > 0:
                    self._execute(symbol, "BUY", buy_qty, current_price, f"swing_signal_conf={confidence:.3f}")
                    return
            elif qty > 0 and swing["sell_signal"]:
                # RISK BOUNDARY: swing exits flatten full position to cap reversal exposure.
                self._execute(symbol, "SELL", qty, current_price, "swing_signal")
                return

        if allow_long and model_buy_signal and qty == 0:
            returns = self.market_buffers[symbol]["Close"].pct_change().dropna()
            mc = self.monte_carlo.stress_test(returns)
            risk_pct = self.strategy_factory.confidence_to_risk_pct(confidence)
            rr = max(0.5, abs(prediction - current_price) / max(current_price * symbol_threshold, 1e-6))
            if self.config.bypass_ror_gate:
                # RISK BOUNDARY: bypass is diagnostic-only and should not be enabled in production by default.
                self.logger.warning("[ROR_GATE][%s] bypass enabled for diagnostics", symbol)
            elif not self.risk_controller.allow_trade_with_ror(
                win_rate=mc["win_rate"],
                reward_risk_ratio=rr,
                risk_fraction=risk_pct,
                mc_var_95=mc["var95"],
            ):
                self.logger.warning(
                    "[ROR_GATE][%s] blocked BUY: win_rate=%.3f var95=%.4f",
                    symbol,
                    mc["win_rate"],
                    mc["var95"],
                )
                return

            alloc = self.account_value * risk_pct
            buy_qty = max(0, int(alloc / max(current_price, 1e-9)))
            if buy_qty > 0:
                self._execute(symbol, "BUY", buy_qty, current_price, f"model_signal_conf={confidence:.3f}")
        elif model_sell_signal and qty > 0:
            if self.cycles_since_buy_by_symbol.get(symbol, 0) >= self.config.buy_cooldown_cycles:
                self._execute(symbol, "SELL", qty, current_price, "model_signal")
            else:
                self.logger.info("Cooldown[%s]: hold position (%d/%d cycles)", symbol, self.cycles_since_buy_by_symbol.get(symbol, 0), self.config.buy_cooldown_cycles)
        elif self.config.log_trade_decisions:
            self.logger.info(
                "HOLD[%s] no trade signal (pred_move=%.2f%% threshold=%.2f%% qty=%d)",
                symbol,
                predicted_move * 100,
                symbol_threshold * 100,
                qty,
            )

    def _evaluate_swing_signal(self, symbol: str, current_price: float, latest_frame: Optional[pd.DataFrame]) -> dict[str, float | bool]:
        frame = latest_frame if latest_frame is not None and not latest_frame.empty else self.market_buffers.get(symbol, pd.DataFrame())
        if frame.empty:
            return {
                "buy_signal": False,
                "sell_signal": False,
                "rsi": 50.0,
                "support": current_price,
                "resistance": current_price,
                "near_support": False,
                "near_resistance": False,
            }

        rsi_col = "RSI_14" if "RSI_14" in frame.columns else ("RSI" if "RSI" in frame.columns else None)
        rsi_value = float(frame.iloc[-1][rsi_col]) if rsi_col else 50.0
        if pd.isna(rsi_value):
            rsi_value = 50.0

        macd_col = "MACD" if "MACD" in frame.columns else None
        macd_signal_col = "MACD_SIGNAL" if "MACD_SIGNAL" in frame.columns else ("MACD_sig" if "MACD_sig" in frame.columns else None)
        macd_cross_up = False
        macd_cross_down = False
        if macd_col and macd_signal_col and len(frame) >= 2:
            prev_macd = float(frame.iloc[-2][macd_col])
            prev_signal = float(frame.iloc[-2][macd_signal_col])
            curr_macd = float(frame.iloc[-1][macd_col])
            curr_signal = float(frame.iloc[-1][macd_signal_col])
            macd_cross_up = prev_macd <= prev_signal and curr_macd > curr_signal
            macd_cross_down = prev_macd >= prev_signal and curr_macd < curr_signal

        window = max(2, int(self.config.swing_sr_window))
        lows = pd.to_numeric(frame.get("Low"), errors="coerce")
        highs = pd.to_numeric(frame.get("High"), errors="coerce")
        support = float(lows.tail(window).min()) if not lows.empty and lows.tail(window).notna().any() else current_price
        resistance = float(highs.tail(window).max()) if not highs.empty and highs.tail(window).notna().any() else current_price
        tolerance = max(0.0, float(self.config.swing_sr_tolerance))
        near_support = current_price <= support * (1 + tolerance)
        near_resistance = current_price >= resistance * (1 - tolerance)

        buy_signal = (rsi_value < float(self.config.swing_rsi_oversold)) or macd_cross_up or near_support
        sell_signal = (rsi_value > float(self.config.swing_rsi_overbought)) or macd_cross_down or near_resistance

        return {
            "buy_signal": buy_signal,
            "sell_signal": sell_signal,
            "rsi": rsi_value,
            "support": support,
            "resistance": resistance,
            "near_support": near_support,
            "near_resistance": near_resistance,
        }

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
                self._attempt_reconnect()
        except Exception as exc:
            self.logger.warning("Heartbeat check failed: %s", exc)
            self._attempt_reconnect()

    def _attempt_reconnect(self) -> None:
        try:
            self.futu_connector.reconnect(max_retries=10, base_delay_seconds=5)
            self._sync_broker_state()
            orders = self.futu_connector.get_open_orders()
            self.logger.info("Reconnect sync completed: open_orders=%d", len(orders.index))
        except Exception as exc:
            self.logger.error("Reconnect failed: %s", exc)
            self._notify(f"[RECONNECT_FAIL] {exc}")

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
        reference_price = self.futu_connector.get_order_reference_price(symbol, side, fallback_price=price)
        fill_price = reference_price if reference_price > 0 else price

        if self.config.auto_trade:
            try:
                if self.config.paper_trading:
                    paper_fill = self.paper_trading_simulator.execute_order(symbol=symbol, side=side, qty=qty, reference_price=fill_price)
                    fill_price = float(paper_fill["fill_price"])
                    paper_pnl = self.paper_trading_simulator.mark_to_market(self.latest_prices)
                    self.account_value = float(paper_pnl.get("equity", self.account_value))
                    self.logger.info("PAPER_ORDER %s %s qty=%d fill=%.4f type=NORMAL", symbol, side, qty, fill_price)
                else:
                    order_result = self.futu_connector.place_order(symbol, qty, side, fill_price)
                    order_id = self.futu_connector.extract_order_id(order_result)
                    status = self.futu_connector.wait_for_fill(order_id)
                    fill_price = float(status.get("filled_avg_price") or fill_price)
            except Exception as exc:
                self.logger.error(
                    "EXEC_FAIL %s %s qty=%d limit=%.4f reason=%s error=%s",
                    symbol,
                    side,
                    qty,
                    fill_price,
                    reason,
                    exc,
                )
                self._notify(f"[EXEC_FAIL] {symbol} {side} qty={qty} reason={reason} error={exc}")
                return

        if side == "BUY":
            self.position_qty_by_symbol[symbol] += qty
            self.highest_price_since_entry_by_symbol[symbol] = fill_price
            self.entry_price_by_symbol[symbol] = fill_price
            self.cycles_since_buy_by_symbol[symbol] = 0
        else:
            self.position_qty_by_symbol[symbol] = max(0, self.position_qty_by_symbol[symbol] - qty)
            if self.position_qty_by_symbol[symbol] == 0:
                self.highest_price_since_entry_by_symbol[symbol] = 0.0
                self.entry_price_by_symbol[symbol] = 0.0
                self.bars_held_by_symbol[symbol] = 0
                self.cycles_since_buy_by_symbol[symbol] = 999999

        self.pnl_tracker.record_fill(symbol=symbol, side=side, qty=qty, price=fill_price)
        pnl_report = self.pnl_tracker.build_report(self.latest_prices)
        self.account_value = self.starting_equity + float(pnl_report.get("net_pnl", 0.0))

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

    def _append_pattern_snapshot(self, symbol: str, pattern: str, confidence: float, prediction: float, current_price: float) -> None:
        out = Path("v3_pipeline/logs/pattern_signals_latest.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "pattern": pattern,
            "confidence": confidence,
            "prediction": prediction,
            "current_price": current_price,
            "predicted_move": (prediction - current_price) / max(current_price, 1e-9),
        }
        if out.exists():
            hist = pd.read_csv(out)
            hist = pd.concat([hist, pd.DataFrame([row])], ignore_index=True).tail(1000)
        else:
            hist = pd.DataFrame([row])
        hist.to_csv(out, index=False)
