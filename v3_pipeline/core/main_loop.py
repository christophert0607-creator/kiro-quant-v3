import asyncio
import logging
import sys
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from notifier import send_tg_msg
from v3_pipeline.core.alpha_engine import KiroAlphaEngine, AlphaConfig
from v3_pipeline.core.futu_connector import FutuConnector
from v3_pipeline.core.history_priming import HistoryPrimer, PrimingConfig
from v3_pipeline.core.monte_carlo import MonteCarloSimulator
from v3_pipeline.core.strategy_factory import StrategyFactory
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator
from v3_pipeline.models.manager import DataPreparer, ModelManager
from v3_pipeline.risk.manager import RiskController


def _build_stderr_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")

    # Console handler (stderr)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # Rotating file handler — logs/v3_live.log (10MB per file, 5 backups kept)
    try:
        from logging.handlers import RotatingFileHandler
        log_dir = Path(__file__).parent.parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        rotating = RotatingFileHandler(
            log_dir / "v3_live.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        logger.addHandler(rotating)
    except Exception as exc:
        sys.stderr.write(f"[_build_stderr_logger] RotatingFileHandler failed: {exc}\n")

    logger.propagate = False
    return logger


@dataclass
class LiveConfig:
    symbol: str = "TSLA"
    symbols_list: list[str] = field(default_factory=list)
    polling_seconds: int = 60
    prediction_threshold: float = float(os.getenv("PREDICTION_THRESHOLD", "0.01"))
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

    # Capital allocation buckets (fractions should sum to 1.0)
    bucket_fractions: dict[str, float] = field(
        default_factory=lambda: {"long": 0.5, "mid": 0.3, "short": 0.1, "reserve": 0.1}
    )
    bucket_by_symbol: dict[str, str] = field(default_factory=dict)

    # Per-bucket model trigger thresholds (fractional; e.g. 0.002 = 0.2%)
    bucket_thresholds: dict[str, float] = field(
        default_factory=lambda: {"long": 0.0020, "mid": 0.0015, "short": 0.0012, "avoid": 0.01}
    )

    max_portfolio_positions: int = 3  # v2: reduced from 8 to limit risk

    # ---- Hard risk caps (2026-04-12 fix) ----
    max_loss_per_trade: float = 30.0   # Hard cap: never lose more than $30 per trade
    max_position_value: float = 2000.0 # Derived: $30 / 1.5% stop_loss = $2000 max position
    min_confidence_threshold: float = 0.20  # Base confidence must be >= 20% (2026-04-14: lowered from 0.35 to allow more signals through)

    # ---- Strategy v2: Entry gates ----
    rsi_oversold_entry: int = 30          # RSI < 30 for oversold entry
    rsi_deep_oversold: int = 20           # RSI < 20 for deep oversold (scale-in)
    sma_filter: bool = True                # Price must be > SMA_20 for LONG
    macd_positive_required: bool = True   # MACD_HIST > 0 for oversold entry
    vix_max: float = 30.0                  # Block all entries if VIX >= 30
    sentiment_min_entry: float = -0.1      # Block if sentiment < -0.1

    # ---- Strategy v3: SHORT Entry gates (Plan B dual-directional) ----
    short_enabled: bool = True             # Enable SHORT (bearish/RSI overbought entries)
    rsi_overbought_entry: int = 70         # RSI > 70 for overbought SHORT entry
    rsi_deep_overbought: int = 80          # RSI > 80 for deep overbought (stronger conviction)
    macd_negative_required: bool = True    # MACD_HIST < 0 for SHORT entry
    sma_filter_short: bool = True           # Price must be < SMA_20 for SHORT
    short_min_confidence_threshold: float = 0.20  # Confidence gate for SHORT entries
    short_sentiment_max: float = 0.10      # Block SHORT if sentiment > 0.10 (too bullish)

    # ---- Strategy v2: Exit ----
    take_profit_rsi20: float = 0.03       # 3% TP when RSI < 20 (deep oversold)
    take_profit_normal: float = 0.02       # 2% TP for normal entry
    stop_loss: float = 0.015              # 1.5% SL (widened from 1%)
    trailing_stop_active: bool = True
    trailing_stop_trigger: float = 0.015   # Activate trailing after 1.5% profit
    trailing_stop_lock: float = 0.0        # Lock at entry price (0% trailing)
    min_hold_minutes: int = 30            # Minimum hold before time-exit (was 5)
    max_hold_minutes: int = 120           # Hard exit after 2 hours
    time_exit_near_entry_pct: float = 0.003  # Exit if within 0.3% of entry after min_hold

    # ---- Strategy v3: SHORT Exit (Plan B) ----
    short_stop_loss: float = 0.015         # 1.5% SL for SHORT positions (price rise = loss)
    short_take_profit: float = 0.02        # 2% TP for SHORT positions
    short_trailing_stop_trigger: float = 0.015  # Activate trailing after 1.5% profit (for SHORT)
    short_trailing_stop_lock: float = 0.0  # Lock at entry for SHORT


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
        # v3 Plan B: track SHORT positions separately (positive = LONG qty, negative = SHORT qty)
        self.short_position_qty_by_symbol: dict[str, int] = {s: 0 for s in self.symbols}
        self.highest_price_since_entry_by_symbol: dict[str, float] = {s: 0.0 for s in self.symbols}
        self.lowest_price_since_short_by_symbol: dict[str, float] = {s: 0.0 for s in self.symbols}  # v3: for SHORT trailing
        self.bars_held_by_symbol: dict[str, int] = {s: 0 for s in self.symbols}
        self.bars_held_short_by_symbol: dict[str, int] = {s: 0 for s in self.symbols}  # v3: SHORT bars held
        self.cycles_since_buy_by_symbol: dict[str, int] = {s: 999999 for s in self.symbols}
        self.cycles_since_short_by_symbol: dict[str, int] = {s: 999999 for s in self.symbols}  # v3
        # Sell confirmation streak (avoid whipsaw exits when sentiment is bullish)
        self.sell_signal_streak_by_symbol: dict[str, int] = {s: 0 for s in self.symbols}
        self.buy_cover_signal_streak_by_symbol: dict[str, int] = {s: 0 for s in self.symbols}  # v3: cover confirmation streak
        self.entry_price_by_symbol: dict[str, float] = {s: 0.0 for s in self.symbols}
        self.short_entry_price_by_symbol: dict[str, float] = {s: 0.0 for s in self.symbols}  # v3: SHORT entry price
        self.entry_rsi_by_symbol: dict[str, float] = {s: 50.0 for s in self.symbols}  # v2: for RSI-gated TP
        self.last_price_by_symbol: dict[str, float] = {}
        self.profile_timings: list[dict] = []
        self.sentiment_score = 0.0
        self.sentiment_summary = "N/A"

        self.equity_peak = 0.0

        # ── Self-Learning: track open signal IDs per symbol ──
        self._signal_id_by_symbol: dict[str, str] = {}
        self._short_signal_id_by_symbol: dict[str, str] = {}  # v3: SHORT signal IDs
        self._pred_id_by_symbol: dict[str, str | None] = {}
        self._short_pred_id_by_symbol: dict[str, str | None] = {}  # v3
        self._entry_time_by_symbol: dict[str, datetime] = {}
        self._short_entry_time_by_symbol: dict[str, datetime] = {}  # v3
        self.account_value = 100000.0
        self.strategy_factory = StrategyFactory()
        self.alpha_engine = KiroAlphaEngine(AlphaConfig(use_all_features=True))  # Use all features (match training)
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
        self._sync_sentiment()
        self.logger.info("[RUN_CYCLE] symbols=%d polling_sec=%d", len(self.symbols), self.config.polling_seconds)

        # ── Technical Screener: rank and filter symbols ─────────────────────
        if os.getenv("ENABLE_SCREENER", "0") == "1":
            try:
                from v3_pipeline.features.screener import ScreenConfig, score_symbols
                cfg = ScreenConfig.from_env()
                ranked = score_symbols(self.market_buffers, {}, {s: 0.0 for s in self.symbols}, cfg)
                # Keep top N + any we already hold (don't skip if in position)
                held = {s for s, qty in self.position_qty_by_symbol.items() if qty > 0}
                passed = {sym for sym, score in ranked[: cfg.top_n * 2]}
                screened = list(passed | held)
                # If screener filters everything out, keep all symbols (fallback)
                if not screened:
                    screened = list(self.symbols)
                    self.logger.info("[SCREENER] No symbols passed filters, using all %d symbols as fallback", len(screened))
                elif screened != list(self.symbols):
                    self.logger.info(
                        "[SCREENER] %d/%d symbols passed: %s",
                        len(screened), len(self.symbols), screened,
                    )
                self.symbols = screened
            except Exception as exc:
                self.logger.warning("[SCREENER] Failed: %s", exc)

        semaphore = asyncio.Semaphore(self.config.max_symbol_concurrency)

        async def guarded(symbol: str) -> None:
            async with semaphore:
                await self._run_symbol_cycle(symbol)

        await asyncio.gather(*(guarded(symbol) for symbol in self.symbols))

    async def _run_symbol_cycle(self, symbol: str) -> None:
        started = time.perf_counter()
        lookback = int(self.model_manager.data_preparer.lookback)

        # ── Warm prediction cache (from IDLE precompute) ──────────────────────
        # If we have a fresh cached prediction from idle precompute, use it to skip quote fetch
        cached = getattr(self, "_idle_pred_cache", {}).get(symbol)
        now = time.time()
        if cached and (now - cached["ts"]) < 300:  # < 5 min = fresh
            self.logger.info("[CACHE_HIT][%s] pred=%.4f conf=%.4f age=%.0fs", symbol, cached["pred"], cached["conf"], now - cached["ts"])
            # Still need to fetch quote for price, but can skip model prediction
            # Fall through to quote fetch below
        else:
            cached = None  # cache miss or stale
        self.logger.info("[CYCLE_START] symbol=%s buffer_len=%d lookback=%d", symbol, len(self.market_buffers.get(symbol, pd.DataFrame())), lookback)

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

        # ── Warm cache: use pre-computed prediction from IDLE if fresh (< 5 min) ─
        cached = getattr(self, "_idle_pred_cache", {}).get(symbol)
        now = time.time()
        if cached and (now - cached["ts"]) < 300:
            prediction = cached["pred"]
            confidence = cached["conf"]
            self.logger.info("[CACHE_HIT][%s] Using cached pred=%.4f conf=%.4f (age=%.0fs)", symbol, prediction, confidence, now - cached["ts"])
        else:
            prediction = float(self.model_manager.predict(wfa_frame, data_preparer=symbol_preparer))
            # 2026-04-14 Fix: Rescale raw price-deviation confidence to meaningful range.
            # Raw ratio = |pred - price| / price. For most stocks this yields 0.001-0.01.
            # Multiply by 50x to map into 0.05-0.50 range (aligns with observed win-rate data).
            # This preserves the directional signal while making thresholds meaningful.
            raw_ratio = abs(prediction - current_price) / max(current_price, 1e-9)
            confidence = min(1.0, raw_ratio * 50.0)

        # Provide latest technical indicators snapshot for optional tactical entries
        latest_ind: dict[str, float] = {}
        try:
            latest_row = featured.iloc[-1]
            for k in ("RSI_14", "MACD_HIST", "SMA_5", "SMA_20", "BB_LOWER", "BB_MIDDLE"):
                if k in latest_row.index:
                    v = latest_row.get(k)
                    if v is not None:
                        latest_ind[k] = float(v)
        except Exception:
            latest_ind = {}

        # ── Self-Learning: record ML prediction WITH indicators for training ──
        _pred_id_for_signal: str | None = None
        try:
            from self_learn import hook_on_prediction
            _pred_id_for_signal = hook_on_prediction(
                symbol, float(prediction), float(confidence), indicators=latest_ind
            )
            self.logger.info("[SELFLEARN] pred_id=%s symbol=%s pred=%.4f conf=%.4f inds=%s",
                _pred_id_for_signal[:8], symbol, prediction, confidence, list(latest_ind.keys()))
        except Exception as _exc:
            self.logger.warning("SelfLearn prediction hook failed for %s: %s", symbol, _exc)

        # Store for signal hook
        self._pred_id_by_symbol[symbol] = _pred_id_for_signal

        vix_value = await asyncio.to_thread(self._get_vix)
        profile = self.strategy_factory.choose_profile(vix_value, self.sentiment_score)

        self._detect_critical_move(symbol, current_price)

        self._run_trading_logic(
            symbol,
            current_price,
            prediction,
            confidence,
            profile.allow_long,
            profile.risk_multiplier,
            latest_ind,
            allow_short=profile.allow_short,
        )

        elapsed_ms = (time.perf_counter() - started) * 1000
        self.profile_timings.append({"symbol": symbol, "elapsed_ms": elapsed_ms, "ts": datetime.now(timezone.utc).isoformat()})

    def _run_trading_logic(
        self,
        symbol: str,
        current_price: float,
        prediction: float,
        confidence: float,
        allow_long: bool,
        risk_multiplier: float,
        indicators: dict[str, float] | None = None,
        allow_short: bool = True,  # v3 Plan B: allow SHORT entries
    ) -> None:
        self.equity_peak = max(self.equity_peak, self.account_value)
        if self.risk_controller.circuit_breaker_triggered(self.equity_peak, self.account_value):
            self._notify(f"🚨 Circuit breaker hit. Equity={self.account_value:.2f}")
            return

        # ---- Strategy v2: Entry Gates ----
        vix = getattr(self, "vix_value", 18.0)
        if vix >= self.config.vix_max:
            self.logger.debug("[VIX_GATE][%s] VIX=%.1f >= %.1f, blocking entry", symbol, vix, self.config.vix_max)
            return

        sentiment = float(getattr(self, "sentiment_score", 0.0) or 0.0)
        if sentiment < self.config.sentiment_min_entry:
            self.logger.debug("[SENTIMENT_GATE][%s] sentiment=%.2f < %.2f, blocking entry", symbol, sentiment, self.config.sentiment_min_entry)
            return

        qty = self.position_qty_by_symbol.get(symbol, 0)
        self.bars_held_by_symbol[symbol] = self.bars_held_by_symbol.get(symbol, 0) + (1 if qty > 0 else 0)
        if qty > 0:
            self.cycles_since_buy_by_symbol[symbol] = self.cycles_since_buy_by_symbol.get(symbol, 0) + 1
        volatility = self.market_buffers[symbol]["Close"].pct_change().rolling(20).std().iloc[-1]
        stop_pct = self.strategy_factory.trailing_stop_by_volatility(float(volatility) if pd.notna(volatility) else 0.0, self.bars_held_by_symbol[symbol])

        if qty > 0:
            entry_price = self.entry_price_by_symbol.get(symbol, current_price)
            if entry_price > 0:
                profit_pct = (current_price - entry_price) / entry_price
                entry_rsi = float(self.entry_rsi_by_symbol.get(symbol, 50.0))
                bars_held = self.bars_held_by_symbol.get(symbol, 0)

                # v2: RSI-gated take profit (3% deep oversold, 2% normal)
                tp_thresh = self.config.take_profit_rsi20 if entry_rsi <= self.config.rsi_deep_oversold else self.config.take_profit_normal
                sl_thresh = self.config.stop_loss  # 1.5% hard stop

                if profit_pct >= tp_thresh:
                    self.logger.info("[%s] Take profit triggered! profit=%.2f%% (threshold=%.2f%%) entry_rsi=%.1f", symbol, profit_pct * 100, tp_thresh * 100, entry_rsi)
                    self._execute(symbol, "SELL", qty, current_price, f"take_profit_rsi{int(entry_rsi)}")
                    self._record_trade_closed(symbol, current_price, qty)
                    return
                elif profit_pct <= -sl_thresh:
                    self.logger.info("[%s] Stop loss triggered! loss=%.2f%% (threshold=%.2f%%)", symbol, profit_pct * 100, sl_thresh * 100)
                    self._execute(symbol, "SELL", qty, current_price, "stop_loss_v2")
                    self._record_trade_closed(symbol, current_price, qty)
                    return

                # v2: Trailing stop after trailing_stop_trigger profit (lock at entry)
                if self.config.trailing_stop_active:
                    trigger_pct = self.config.trailing_stop_trigger
                    lock_pct = self.config.trailing_stop_lock
                    if profit_pct >= trigger_pct:
                        trailing_stop_price = entry_price * (1 + lock_pct)  # lock at entry (0% loss)
                        if current_price < trailing_stop_price:
                            self.logger.info("[%s] Trailing stop! profit=%.2f%% trailing_stop=%.4f", symbol, profit_pct * 100, trailing_stop_price)
                            self._execute(symbol, "SELL", qty, current_price, "trailing_stop")
                            self._record_trade_closed(symbol, current_price, qty)
                            return

                # v2: Time exit — after min_hold, if price stuck near entry
                min_hold_bars = self.config.min_hold_minutes  # in minutes (polling=60s, so ~1 bar/min)
                if bars_held >= min_hold_bars:
                    near_entry = abs(profit_pct) <= self.config.time_exit_near_entry_pct
                    max_hold_bars = self.config.max_hold_minutes
                    if near_entry or bars_held >= max_hold_bars:
                        self.logger.info("[%s] Time exit! profit=%.2f%% bars_held=%d min_hold=%d max_hold=%d", symbol, profit_pct * 100, bars_held, min_hold_bars, max_hold_bars)
                        self._execute(symbol, "SELL", qty, current_price, "time_exit_v2")
                        self._record_trade_closed(symbol, current_price, qty)
                        return

            self.highest_price_since_entry_by_symbol[symbol] = max(self.highest_price_since_entry_by_symbol.get(symbol, 0.0), current_price)
            stop_price = self.highest_price_since_entry_by_symbol[symbol] * (1 - stop_pct)
            if current_price < stop_price:
                self._execute(symbol, "SELL", qty, current_price, "time_decay_vol_stop")
                self._record_trade_closed(symbol, current_price, qty)
                return

        # ── Strategy v3 Plan B: SHORT Position Exit Logic ──────────────────────
        # Handles: take-profit, stop-loss, trailing stop, time-exit for SHORT positions
        short_qty = int(self.short_position_qty_by_symbol.get(symbol, 0) or 0)
        if short_qty > 0:
            short_entry_price = float(self.short_entry_price_by_symbol.get(symbol, 0.0) or 0.0)
            bars_held_short = int(self.bars_held_short_by_symbol.get(symbol, 0) or 0)
            self.bars_held_short_by_symbol[symbol] = bars_held_short + 1
            self.cycles_since_short_by_symbol[symbol] = 0

            if short_entry_price > 0:
                # SHORT profit = entry - current (price dropped = gain, price rose = loss)
                short_profit_pct = (short_entry_price - current_price) / short_entry_price
                sl_thresh = float(self.config.short_stop_loss)
                tp_thresh = float(self.config.short_take_profit)

                # Take-profit: price fell enough (short wins)
                if short_profit_pct >= tp_thresh:
                    self.logger.info(
                        "[SHORT_TP][%s] short_tp=%.2f%% profit=%.2f%% bars=%d",
                        symbol, tp_thresh * 100, short_profit_pct * 100, bars_held_short,
                    )
                    self._execute_short_exit(symbol, short_qty, current_price, "short_take_profit")
                    self._record_short_closed(symbol, current_price, short_qty)
                    return

                # Stop-loss: price rose enough (short loses)
                if short_profit_pct <= -sl_thresh:
                    self.logger.info(
                        "[SHORT_SL][%s] short_sl=%.2f%% profit=%.2f%% bars=%d",
                        symbol, sl_thresh * 100, short_profit_pct * 100, bars_held_short,
                    )
                    self._execute_short_exit(symbol, short_qty, current_price, "short_stop_loss")
                    self._record_short_closed(symbol, current_price, short_qty)
                    return

                # Trailing stop for SHORT: track lowest price since entry
                self.lowest_price_since_short_by_symbol[symbol] = min(
                    self.lowest_price_since_short_by_symbol.get(symbol, current_price), current_price
                )
                trailing_trigger = float(self.config.short_trailing_stop_trigger)
                trailing_lock = float(self.config.short_trailing_stop_lock)
                if short_profit_pct >= trailing_trigger:
                    # For SHORT: lock in profit by setting a floor price
                    # Trailing stop fires if price rises above: lowest_since_entry * (1 + trailing_lock)
                    trailing_floor = self.lowest_price_since_short_by_symbol[symbol] * (1 + trailing_lock)
                    if current_price > trailing_floor:
                        self.logger.info(
                            "[SHORT_TRAILING][%s] profit=%.2f%% floor=%.4f curr=%.4f",
                            symbol, short_profit_pct * 100, trailing_floor, current_price,
                        )
                        self._execute_short_exit(symbol, short_qty, current_price, "short_trailing_stop")
                        self._record_short_closed(symbol, current_price, short_qty)
                        return

                # Time exit for SHORT
                min_hold_short = int(self.config.min_hold_minutes)
                max_hold_short = int(self.config.max_hold_minutes)
                if bars_held_short >= min_hold_short:
                    near_entry_short = abs(short_profit_pct) <= self.config.time_exit_near_entry_pct
                    if near_entry_short or bars_held_short >= max_hold_short:
                        self.logger.info(
                            "[SHORT_TIME_EXIT][%s] profit=%.2f%% bars=%d max=%d",
                            symbol, short_profit_pct * 100, bars_held_short, max_hold_short,
                        )
                        self._execute_short_exit(symbol, short_qty, current_price, "short_time_exit")
                        self._record_short_closed(symbol, current_price, short_qty)
                        return

                # Buy-to-cover confirmation streak (mirror of sell_signal_streak)
                # When model prediction goes UP > threshold_up, cover the SHORT
                if prediction > threshold_up and self.cycles_since_short_by_symbol.get(symbol, 999999) >= self.config.buy_cooldown_cycles:
                    sentiment_score = float(getattr(self, "sentiment_score", 0.0) or 0.0)
                    required = 1
                    if sentiment_score >= 0.50:
                        required = 3
                    elif sentiment_score >= 0.10:
                        required = 2
                    streak = int(self.buy_cover_signal_streak_by_symbol.get(symbol, 0)) + 1
                    self.buy_cover_signal_streak_by_symbol[symbol] = streak
                    if streak >= required:
                        self.buy_cover_signal_streak_by_symbol[symbol] = 0
                        self.logger.info(
                            "[SHORT_COVER][%s] streak=%d/%d sentiment=%.2f → COVER",
                            symbol, streak, required, sentiment_score,
                        )
                        self._execute_short_exit(symbol, short_qty, current_price, "short_cover_model_confirmed")
                        self._record_short_closed(symbol, current_price, short_qty)
                        return
                    else:
                        self.logger.info(
                            "[SHORT_COVER_WAIT][%s] streak=%d/%d sentiment=%.2f (holding short)",
                            symbol, streak, required, sentiment_score,
                        )
                else:
                    self.buy_cover_signal_streak_by_symbol[symbol] = 0
        else:
            # No SHORT position: reset streak
            self.buy_cover_signal_streak_by_symbol[symbol] = 0

        bucket = str(self.config.bucket_by_symbol.get(symbol, "mid"))
        bucket_th = float(self.config.bucket_thresholds.get(bucket, self.config.prediction_threshold))
        # Priority: per-symbol override > bucket default > global default
        symbol_threshold = float(self.config.prediction_thresholds.get(symbol, bucket_th))
        threshold_up = current_price * (1 + symbol_threshold)
        threshold_down = current_price * (1 - symbol_threshold)

        trace_base = {
            "symbol": symbol,
            "bucket": bucket,
            "px": float(current_price),
            "prediction": float(prediction),
            "confidence": float(confidence),
            "threshold": float(symbol_threshold),
            "threshold_up": float(threshold_up),
            "threshold_down": float(threshold_down),
            "sentiment_score": float(getattr(self, "sentiment_score", 0.0) or 0.0),
            "sentiment_summary": str(getattr(self, "sentiment_summary", "N/A")),
            "allow_long": bool(allow_long),
            "risk_multiplier": float(risk_multiplier),
            "position_qty": int(self.position_qty_by_symbol.get(symbol, 0) or 0),
        }

        # Default buy reason tag (can be overridden by tactical signals)
        buy_reason = f"model_signal_conf={confidence:.3f}"
        short_reason = f"short_model_conf={confidence:.3f}"  # v3: SHORT reason tag

        # ---- Strategy v2: RSI Oversold Entry with MACD + SMA confirmation ----
        if (
            qty == 0
            and allow_long
            and float(self.sentiment_score) >= self.config.sentiment_min_entry
            and indicators
        ):
            rsi = float(indicators.get("RSI_14", 50.0))
            macd_hist = float(indicators.get("MACD_HIST", 0.0))
            sma5 = float(indicators.get("SMA_5", current_price))
            sma20 = float(indicators.get("SMA_20", current_price))
            bb_lower = float(indicators.get("BB_LOWER", current_price))

            # v2: Require MACD positive + (if sma_filter) price > SMA_20
            macd_ok = not self.config.macd_positive_required or macd_hist > 0.0
            trend_ok = not self.config.sma_filter or current_price >= sma20

            # Deep oversold RSI: force BUY (RSI-gated TP applied at exit)
            if rsi <= self.config.rsi_oversold_entry and macd_ok and trend_ok:
                self.logger.info(
                    "[RSI_OVERSOLD_BUY][%s]: rsi=%.1f macd_hist=%.4f px=%.4f sma20=%.4f trend_ok=%s",
                    symbol, rsi, macd_hist, current_price, sma20, trend_ok,
                )
                buy_reason = "rsi_oversold_v2"
                confidence = max(confidence, 0.50)
                prediction = current_price * 1.001  # predict slightly above current to trigger
            # Normal tech entry: RSI moderate + MACD positive + price above SMA_5
            elif bucket == "short" and (rsi <= 35.0) and (macd_hist > 0.0) and (current_price >= sma5) and (current_price <= bb_lower * 1.01) and trend_ok:
                self.logger.info(
                    "TECH_BUY[%s]: rsi=%.1f macd_hist=%.4f px=%.4f sma5=%.4f sma20=%.4f",
                    symbol,
                    rsi,
                    macd_hist,
                    current_price,
                    sma5,
                    sma20,
                )
                buy_reason = "tech_entry_v2"
                confidence = max(confidence, 0.35)
                prediction = threshold_up * 1.0002

        # ── 2026-04-12 Fix: Confidence hard gate ──────────────────────────────────
        # Block if raw confidence is below threshold AND no RSI/MACD entry was triggered.
        # RSI/MACD entries set buy_reason themselves; absence means pure model signal.
        if allow_long and qty == 0:
            min_conf = float(getattr(self.config, 'min_confidence_threshold', 0.35))
            raw_conf_ok = confidence >= min_conf
            is_rsiedge_entry = buy_reason in ("rsi_oversold_v2", "tech_entry_v2")
            if not raw_conf_ok and not is_rsiedge_entry:
                self.logger.info(
                    "[CONF_GATE][%s] blocked: raw_conf=%.4f < %.2f (no RSI/MACD edge)",
                    symbol, confidence, min_conf,
                )
                self._append_decision_trace({
                    **trace_base,
                    "action": "BUY_BLOCKED_LOW_CONF",
                    "raw_confidence": float(confidence),
                    "min_confidence": min_conf,
                })
                return

        # ── Strategy v3 Plan B: RSI Overbought SHORT Entry ───────────────────────
        # Mirror of RSI_OVERSOLD_BUY: triggers when RSI > 70 and MACD < 0
        short_qty = int(self.short_position_qty_by_symbol.get(symbol, 0) or 0)
        if (
            short_qty == 0
            and allow_short
            and float(getattr(self, "sentiment_score", 0.0) or 0.0) <= self.config.short_sentiment_max
            and self.config.short_enabled
            and indicators
        ):
            rsi = float(indicators.get("RSI_14", 50.0))
            macd_hist = float(indicators.get("MACD_HIST", 0.0))
            sma5 = float(indicators.get("SMA_5", current_price))
            sma20 = float(indicators.get("SMA_20", current_price))

            # MACD must be negative for SHORT (downtrend confirmation)
            macd_short_ok = not self.config.macd_negative_required or macd_hist < 0.0
            # Price must be below SMA_20 for SHORT (downtrend confirmation)
            trend_short_ok = not self.config.sma_filter_short or current_price <= sma20

            # Deep overbought RSI > 80: strong SHORT conviction
            if rsi >= self.config.rsi_deep_overbought and macd_short_ok and trend_short_ok:
                self.logger.info(
                    "[RSI_OVERBOUGHT_SHORT][%s]: rsi=%.1f macd_hist=%.4f px=%.4f sma20=%.4f trend_short_ok=%s",
                    symbol, rsi, macd_hist, current_price, sma20, trend_short_ok,
                )
                short_reason = "rsi_overbought_short_v3"
                confidence = max(confidence, 0.55)
                prediction = current_price * 0.999  # push below current to trigger SHORT
            # Normal overbought RSI > 70: standard SHORT entry
            elif rsi >= self.config.rsi_overbought_entry and macd_short_ok and trend_short_ok:
                self.logger.info(
                    "[RSI_OVERBOUGHT_SHORT][%s]: rsi=%.1f macd_hist=%.4f px=%.4f sma20=%.4f trend_short_ok=%s",
                    symbol, rsi, macd_hist, current_price, sma20, trend_short_ok,
                )
                short_reason = "rsi_overbought_short_v3"
                confidence = max(confidence, 0.40)
                prediction = current_price * 0.9995  # push below current to trigger SHORT

        # ── SHORT Entry: prediction < threshold_down ───────────────────────────────
        # Only enter SHORT if: no LONG position, no SHORT position, sentiment allows
        if (
            allow_short
            and prediction < threshold_down
            and short_qty == 0
            and qty == 0
        ):
            # Confidence gate for SHORT
            min_conf_short = float(getattr(self.config, 'short_min_confidence_threshold', 0.20))
            raw_conf_short_ok = confidence >= min_conf_short
            is_short_tactical = short_reason in ("rsi_overbought_short_v3",)
            if not raw_conf_short_ok and not is_short_tactical:
                self.logger.info(
                    "[SHORT_CONF_GATE][%s] blocked: raw_conf=%.4f < %.2f (no RSI-overbought edge)",
                    symbol, confidence, min_conf_short,
                )
                self._append_decision_trace({
                    **trace_base,
                    "action": "SHORT_BLOCKED_LOW_CONF",
                    "raw_confidence": float(confidence),
                    "min_confidence": min_conf_short,
                })
            else:
                returns = self.market_buffers[symbol]["Close"].pct_change().dropna()
                mc = self.monte_carlo.stress_test(returns)
                risk_pct = self.strategy_factory.confidence_to_risk_pct(confidence)
                rr = max(0.5, abs(prediction - current_price) / max(current_price * symbol_threshold, 1e-6))
                if not self.risk_controller.allow_trade_with_ror(
                    win_rate=mc["win_rate"],
                    reward_risk_ratio=rr,
                    risk_fraction=risk_pct,
                    mc_var_95=mc["var95"],
                ):
                    self.logger.warning(
                        "[SHORT_ROR_GATE][%s] blocked SHORT: win_rate=%.3f var95=%.4f",
                        symbol, mc["win_rate"], mc["var95"],
                    )
                else:
                    active_positions = sum(1 for q in self.position_qty_by_symbol.values() if q > 0)
                    active_short_positions = sum(1 for q in self.short_position_qty_by_symbol.values() if q > 0)
                    if (active_positions + active_short_positions) >= int(self.config.max_portfolio_positions):
                        self.logger.info("[%s] Max positions (%d) reached, skipping SHORT",
                            symbol, int(self.config.max_portfolio_positions))
                    else:
                        # SHORT capital allocation: same bucket fractions as LONG
                        bucket = str(self.config.bucket_by_symbol.get(symbol, "mid"))
                        fracs = dict(self.config.bucket_fractions or {})
                        reserve_frac = float(fracs.get("reserve", 0.1))
                        bucket_frac = float(fracs.get(bucket, 0.0))
                        reserve_frac = max(0.0, min(0.9, reserve_frac))
                        bucket_frac = max(0.0, min(1.0, bucket_frac))

                        total_cap = self.account_value * (1.0 - reserve_frac)
                        bucket_cap = self.account_value * bucket_frac
                        available_total = max(0.0, total_cap - sum(
                            int(self.position_qty_by_symbol.get(s, 0) or 0) * float(self.last_price_by_symbol.get(s, 0.0) or 0.0)
                            + int(self.short_position_qty_by_symbol.get(s, 0) or 0) * float(self.last_price_by_symbol.get(s, 0.0) or 0.0)
                            for s in self.symbols
                        ))
                        available_bucket = max(0.0, bucket_cap - sum(
                            int(self.short_position_qty_by_symbol.get(s, 0) or 0) * float(self.last_price_by_symbol.get(s, 0.0) or 0.0)
                            for s in self.symbols
                            if str(self.config.bucket_by_symbol.get(s, "mid")) == bucket
                        ))
                        if available_total <= 0 or available_bucket <= 0:
                            self.logger.info(
                                "[SHORT_CAP_ALLOC][%s]: bucket=%s avail_total=%.2f avail_bucket=%.2f (skip SHORT)",
                                symbol, bucket, available_total, available_bucket,
                            )
                            self._append_decision_trace({
                                **trace_base,
                                "action": "SHORT_BLOCKED_BUCKET_CAP",
                                "avail_total": float(available_total),
                                "avail_bucket": float(available_bucket),
                                "short_reason": str(short_reason),
                            })
                        else:
                            alloc = available_bucket * 0.10 * float(risk_multiplier)
                            short_alloc_qty = max(0, int(alloc / max(current_price, 1e-9)))
                            max_pos_value = float(getattr(self.config, 'max_position_value', 2000.0))
                            short_alloc_qty = min(short_alloc_qty, max(0, int(max_pos_value / max(current_price, 1e-9))))
                            short_alloc_qty = self._round_to_lot(short_alloc_qty, symbol)
                            if short_alloc_qty > 0:
                                self.logger.info(
                                    "[SHORT_ENTRY][%s] qty=%d price=%.4f reason=%s conf=%.4f",
                                    symbol, short_alloc_qty, current_price, short_reason, confidence,
                                )
                                self._append_decision_trace({
                                    **trace_base,
                                    "action": "SHORT_PLACED",
                                    "short_qty": int(short_alloc_qty),
                                    "alloc": float(alloc),
                                    "short_reason": str(short_reason),
                                    "risk_pct": float(risk_pct),
                                })
                                self._execute_short_entry(symbol, short_alloc_qty, current_price, short_reason, indicators)
                            else:
                                self._append_decision_trace({
                                    **trace_base,
                                    "action": "SHORT_SKIPPED_QTY0",
                                    "alloc": float(alloc),
                                })
            # Return after processing SHORT to prevent LONG logic from also running
            return

        if allow_long and prediction > threshold_up and qty == 0:
            returns = self.market_buffers[symbol]["Close"].pct_change().dropna()
            mc = self.monte_carlo.stress_test(returns)
            risk_pct = self.strategy_factory.confidence_to_risk_pct(confidence)
            rr = max(0.5, abs(prediction - current_price) / max(current_price * symbol_threshold, 1e-6))
            if not self.risk_controller.allow_trade_with_ror(
                win_rate=mc["win_rate"],
                reward_risk_ratio=rr,
                risk_fraction=risk_pct,
                mc_var_95=mc["var95"],
            ):
                self.logger.warning(
                    "[ROR_GATE][%s] blocked BUY: win_rate=%.3f var95=%.4f",
                    symbol,
                    mc["win_rate"],                    mc["var95"],
                )
                return

            active_positions = sum(1 for q in self.position_qty_by_symbol.values() if q > 0)
            if active_positions >= int(self.config.max_portfolio_positions):
                self.logger.info("[%s] Max portfolio positions (%d) reached, skipping buy", symbol, int(self.config.max_portfolio_positions))
                self._append_decision_trace(
                    {
                        **trace_base,
                        "action": "BUY_BLOCKED_MAX_POS",
                        "active_positions": int(active_positions),
                        "max_positions": int(self.config.max_portfolio_positions),
                    }
                )
                return

            # ---- Capital buckets: 50% long / 30% mid / 10% short / 10% reserve ----
            bucket = str(self.config.bucket_by_symbol.get(symbol, "mid"))
            fracs = dict(self.config.bucket_fractions or {})
            reserve_frac = float(fracs.get("reserve", 0.1))
            bucket_frac = float(fracs.get(bucket, 0.0))

            # Safety clamp
            reserve_frac = max(0.0, min(0.9, reserve_frac))
            bucket_frac = max(0.0, min(1.0, bucket_frac))

            # Compute invested value (approx) using last known prices
            total_invested = 0.0
            invested_by_bucket = 0.0
            for sym, q in self.position_qty_by_symbol.items():
                if q <= 0:
                    continue
                px = float(self.last_price_by_symbol.get(sym, 0.0) or 0.0)
                if px <= 0 and sym in self.market_buffers and not self.market_buffers[sym].empty:
                    try:
                        px = float(self.market_buffers[sym].iloc[-1]["Close"])
                    except Exception:
                        px = 0.0
                val = float(q) * max(px, 0.0)
                total_invested += val
                if str(self.config.bucket_by_symbol.get(sym, "mid")) == bucket:
                    invested_by_bucket += val

            total_cap = self.account_value * (1.0 - reserve_frac)
            bucket_cap = self.account_value * bucket_frac

            available_total = max(0.0, total_cap - total_invested)
            available_bucket = max(0.0, bucket_cap - invested_by_bucket)

            if available_total <= 0 or available_bucket <= 0:
                self.logger.info(
                    "CAP_ALLOC[%s]: bucket=%s avail_total=%.2f avail_bucket=%.2f (skip buy)",
                    symbol,
                    bucket,
                    available_total,
                    available_bucket,
                )
                self._append_decision_trace(
                    {
                        **trace_base,
                        "action": "BUY_BLOCKED_BUCKET_CAP",
                        "avail_total": float(available_total),
                        "avail_bucket": float(available_bucket),
                    }
                )
                return

            # Apply regime multiplier to risk sizing
            risk_pct = float(risk_pct) * float(max(0.1, risk_multiplier))

            max_alloc = self.account_value * 0.25
            alloc = min(self.account_value * risk_pct, max_alloc, available_total, available_bucket)
            # Meta-labeling hard gate (US SIM) — blocks BUY when meta prob < threshold.
            try:
                from v3_pipeline.ml import meta_gate as _meta_gate

                if _meta_gate.enabled():
                    # Build full feature set matching training (confidence, snapshot_*, ohlcv_*, ind_*)
                    latest = indicators if indicators else {}  # passed-in indicators dict
                    # Snapshot proxies from live account state
                    total_mv = sum(
                        float(self.position_qty_by_symbol.get(s, 0) or 0) *
                        float(self.market_buffers.get(s, {}).get("Close", [float(self.account_value)])[-1] or self.account_value)
                        for s in self.symbols
                    )
                    snap_total = float(self.account_value)
                    snap_mv = float(total_mv)
                    snap_cash = max(0.0, snap_total - snap_mv)
                    # OHLCV from current bar
                    ohlcv_v = float(latest.get("Volume", 0) or 0)

                    meta_features = {
                        # Core decision features (always available)
                        "prediction": float(prediction),
                        "confidence": float(confidence),
                        "sentiment_score": float(getattr(self, "sentiment_score", 0.0) or 0.0),
                        "bucket": str(bucket),
                        "mc_win_rate": float(mc.get("win_rate", 0.0) or 0.0),
                        "mc_var95": float(mc.get("var95", 0.0) or 0.0),
                        "risk_pct": float(risk_pct),
                        "rr": float(rr),
                        "threshold": float(symbol_threshold),
                        # Snapshot features (match training schema)
                        "snapshot_total_assets": snap_total,
                        "snapshot_cash": snap_cash,
                        "snapshot_market_val": snap_mv,
                        # OHLCV + indicator features (match training schema)
                        "ohlcv_volume": ohlcv_v,
                        "ind_sma_5": float(latest.get("SMA_5", 0) or 0),
                        "ind_sma_20": float(latest.get("SMA_20", 0) or 0),
                        "ind_rsi_14": float(latest.get("RSI_14", 0) or 0),
                        "ind_macd": float(latest.get("MACD_HIST", 0) or 0),
                        "ind_bb_upper": float(latest.get("BB_UPPER", 0) or 0),
                        "ind_bb_lower": float(latest.get("BB_LOWER", 0) or 0),
                    }
                    p = _meta_gate.score(meta_features)
                    thr = float(os.getenv("META_THRESHOLD", "0.5"))

                    policy = _meta_gate.no_score_policy()
                    size_mult = float(_meta_gate.no_score_size_mult())

                    if p is None:
                        # Meta enabled but no model/score available.
                        self._append_decision_trace({**trace_base, "action": "META_NO_SCORE", "meta_thr": thr, "meta_policy": policy, "meta_size_mult": size_mult})
                        if policy == "block":
                            return
                        if policy == "allow_small":
                            alloc = float(alloc) * max(0.0, min(1.0, size_mult))
                    elif p < thr:
                        self.logger.info("META_BLOCK[%s]: p=%.3f<thr=%.2f", symbol, p, thr)
                        self._append_decision_trace({**trace_base, "action": "META_BLOCKED", "meta_p": float(p), "meta_thr": thr})
                        return
                    else:
                        self._append_decision_trace({**trace_base, "action": "META_ALLOWED", "meta_p": float(p), "meta_thr": thr})
            except Exception as _exc:
                self.logger.warning("meta_gate error: %s", _exc)

            buy_qty = max(0, int(alloc / max(current_price, 1e-9)))
            # HARD CAP: never allocate more than max_position_value ($30 loss / 1.5% SL = $2000)
            max_pos_value = float(getattr(self.config, 'max_position_value', 2000.0))
            buy_qty = min(buy_qty, max(0, int(max_pos_value / max(current_price, 1e-9))))
            # Round to HK board lot size (skip HK stocks for now)
            buy_qty = self._round_to_lot(buy_qty, symbol)
            if buy_qty > 0:
                self._append_decision_trace(
                    {
                        **trace_base,
                        "action": "BUY_PLACED",
                        "buy_qty": int(buy_qty),
                        "alloc": float(alloc),
                        "buy_reason": str(buy_reason),
                        "risk_pct": float(risk_pct),
                    }
                )
                self._execute(symbol, "BUY", buy_qty, current_price, buy_reason, indicators)

                # ── Self-Learning: log BUY signal ──
                try:
                    from self_learn import hook_on_signal
                    _sig_id = hook_on_signal(
                        action="BUY",
                        prediction_id=self._pred_id_by_symbol.get(symbol),
                        entry_price=float(current_price),
                        size=int(buy_qty),
                    )
                    self._signal_id_by_symbol[symbol] = _sig_id
                    self._entry_time_by_symbol[symbol] = datetime.now(timezone.utc)
                except Exception:
                    pass
            else:
                self._append_decision_trace(
                    {
                        **trace_base,
                        "action": "BUY_SKIPPED_QTY0",
                        "alloc": float(alloc),
                        "risk_pct": float(risk_pct),
                    }
                )
        elif prediction < threshold_down and qty > 0:
            if self.cycles_since_buy_by_symbol.get(symbol, 0) >= self.config.buy_cooldown_cycles:
                # SELL confirmation when sentiment is not bearish
                # sentiment >= 0.10  -> require 2 consecutive SELL signals
                # sentiment >= 0.50  -> require 3 consecutive SELL signals
                required = 1
                if self.sentiment_score >= 0.50:
                    required = 3
                elif self.sentiment_score >= 0.10:
                    required = 2

                streak = int(self.sell_signal_streak_by_symbol.get(symbol, 0)) + 1
                self.sell_signal_streak_by_symbol[symbol] = streak

                if streak >= required:
                    self.sell_signal_streak_by_symbol[symbol] = 0
                    self._append_decision_trace(
                        {
                            **trace_base,
                            "action": "SELL_PLACED",
                            "sell_qty": int(qty),
                            "sell_reason": "model_signal_confirmed",
                            "sell_required": int(required),
                        }
                    )
                    self._execute(symbol, "SELL", qty, current_price, "model_signal_confirmed")
                    self._record_trade_closed(symbol, current_price, qty)
                else:
                    self.logger.info(
                        "SELL_CONFIRM[%s]: streak=%d/%d sentiment=%.2f (holding)",
                        symbol,
                        streak,
                        required,
                        float(self.sentiment_score),
                    )
            else:
                self.logger.info("Cooldown[%s]: hold position (%d/%d cycles)", symbol, self.cycles_since_buy_by_symbol.get(symbol, 0), self.config.buy_cooldown_cycles)
        else:
            # Reset streak when model no longer asks to sell (or no position)
            if qty == 0:
                self.sell_signal_streak_by_symbol[symbol] = 0
            else:
                self.sell_signal_streak_by_symbol[symbol] = 0

    def _round_to_lot(self, qty: int, symbol: str) -> int:
        """Skip HK stocks entirely (complex lot sizes) - HK market not supported yet."""
        if symbol.endswith(".HK"):
            self.logger.info("SKIP HK symbol %s (lot size complexity not handled)", symbol)
            return 0  # skip HK stocks
        return qty  # US stocks: no rounding needed (lot_size=1)

    def _normalize_market_buffer(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        # Remove non-OHLCV/non-indicator columns (data_source, etc.)
        for col in ["data_source"]:
            if col in out.columns:
                out.drop(columns=[col], inplace=True)
        return (
            out.dropna(subset=["Date"])
            .sort_values("Date")
            .drop_duplicates(subset=["Date"], keep="last")
            .reset_index(drop=True)
        )

    def _get_vix(self) -> float:
        # Use ^VIX (US VIX) directly with yfinance to avoid ^VIX.HK misresolution
        try:
            import yfinance as yf
            ticker = yf.Ticker("^VIX")
            hist = ticker.fast_info
            price = getattr(hist, 'last_price', None) or getattr(hist, 'lastPrice', None)
            if price and price > 0:
                return float(price)
        except Exception:
            pass
        return 18.0  # Safe default

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
        # In NO_FUTU mode, try to load positions from state.json as fallback
        import os as _os
        state_file = _os.path.join(_os.path.dirname(__file__), "..", "..", "state.json")

        try:
            assets = self.futu_connector.get_sync_assets()
            self.account_value = float(assets.get("total_assets", self.account_value))
        except Exception as exc:
            self.logger.warning("Asset sync failed: %s", exc)

        # In auto_trade=True mode: trust local position state maintained by _execute().
        # In this mode, _execute() immediately updates position_qty_by_symbol after orders,
        # so we don't need to re-read from FutuOpenD (which may be stale in paper/SIM mode).
        # _sync_broker_state() still runs to sync assets.
        if self.config.auto_trade:
            pass  # positions tracked locally by _execute(), don't overwrite from stale FutuOpenD
        else:
            try:
                positions = self.futu_connector.get_sync_positions()
                for symbol in self.symbols:
                    qty = 0
                    if not positions.empty and "code" in positions.columns:
                        code, _ = self.futu_connector.resolve_symbol(symbol)
                        row = positions[positions["code"] == code]
                        if not row.empty:
                            qty = int(float(row.iloc[0].get("qty", row.iloc[0].get("can_sell_qty", 0))))
                    self.position_qty_by_symbol[symbol] = max(0, qty)
            except Exception as exc:
                self.logger.warning("Position sync failed: %s", exc)

        # NO_FUTU fallback: if positions are all zero, load from state.json
        if _os.getenv("NO_FUTU") == "1" and _os.path.exists(state_file):
            all_zero = all(q == 0 for q in self.position_qty_by_symbol.values())
            if all_zero:
                try:
                    import json
                    state = json.load(open(state_file))
                    positions_map = state.get("positions", {})
                    for symbol in self.symbols:
                        for key, qty in positions_map.items():
                            norm = key.replace("US.", "").replace(".HK", "HK")
                            if symbol.replace(".HK", "HK") == norm or symbol == key:
                                self.position_qty_by_symbol[symbol] = max(0, int(qty))
                                self.logger.info("Position sync [state.json]: %s = %d", symbol, int(qty))
                                break
                except Exception:
                    pass

    def _execute(self, symbol: str, side: str, qty: int, price: float, reason: str, indicators: dict | None = None) -> None:
        # Guard: 所有副作用（position mutation + 下單）必須喺同一個 auto_trade block 內
        if not self.config.auto_trade:
            return

        reference_price = self.futu_connector.get_order_reference_price(symbol, side, fallback_price=price)
        fill_price = reference_price if reference_price > 0 else price

        # Reset confirmation streak on any execution
        try:
            self.sell_signal_streak_by_symbol[symbol] = 0
        except Exception:
            pass

        # Apply lot-size rounding before any order
        qty = self._round_to_lot(qty, symbol) if symbol.endswith(".HK") else qty
        if qty <= 0:
            self.logger.info("SKIP %s %s (lot rounding → qty=0)", symbol, side)
            return

        if side == "BUY":
            self.position_qty_by_symbol[symbol] += qty
            self.highest_price_since_entry_by_symbol[symbol] = fill_price
            self.entry_price_by_symbol[symbol] = fill_price
            self.cycles_since_buy_by_symbol[symbol] = 0
            # v2: Store entry RSI for RSI-gated take profit at exit
            if indicators:
                self.entry_rsi_by_symbol[symbol] = float(indicators.get("RSI_14", 50.0))
        else:
            self.position_qty_by_symbol[symbol] = max(0, self.position_qty_by_symbol[symbol] - qty)
            if self.position_qty_by_symbol[symbol] == 0:
                self.highest_price_since_entry_by_symbol[symbol] = 0.0
                self.bars_held_by_symbol[symbol] = 0
                self.cycles_since_buy_by_symbol[symbol] = 999999

        if self.config.paper_trading:
            self.logger.info("PAPER_ORDER %s %s qty=%d limit=%.4f type=NORMAL", symbol, side, qty, fill_price)
        else:
            self.futu_connector.place_order(symbol, qty, side, fill_price)

        self.logger.info("EXEC %s %s qty=%d fill=%.4f reason=%s", symbol, side, qty, fill_price, reason)
        
        # Log to trades.jsonl
        import os as _os
        import json as _json
        trade_log = _os.path.join(_os.path.dirname(__file__), "..", "..", "trades.jsonl")
        trade_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": float(fill_price),
            "reason": reason,
            "paper": bool(self.config.paper_trading),
        }
        try:
            with open(trade_log, "a", encoding="utf-8") as f:
                f.write(_json.dumps(trade_entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.logger.warning("Failed to log trade to JSONL: %s", exc)

        # Persist positions into state.json to avoid repeated SELL/BUY after restart
        # Works in both paper (NO_FUTU=1) and real/SIM trading (NO_FUTU=0) modes
        state_file = _os.path.join(_os.path.dirname(__file__), "..", "..", "state.json")
        try:
            state: dict = {}
            if _os.path.exists(state_file):
                with open(state_file, "r", encoding="utf-8") as f:
                    state = _json.load(f) or {}

            code, _ = self.futu_connector.resolve_symbol(symbol)
            state["date"] = datetime.now(timezone.utc).date().isoformat()
            state["daily_trades"] = int(state.get("daily_trades", 0)) + 1

            positions = state.setdefault("positions", {})
            positions[code] = int(self.position_qty_by_symbol.get(symbol, 0))

            last_orders = state.setdefault("last_orders", {})
            last_orders[code] = {
                "time": time.time(),
                "signal": side,
                "qty": int(qty),
                "price": float(fill_price),
                "reason": reason,
            }

            with open(state_file, "w", encoding="utf-8") as f:
                f.write(_json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        except Exception as exc:
            self.logger.warning("Failed to persist state.json: %s", exc)

    # ── Self-Learning: record closed position outcome ──────────────────────────
    def _record_trade_closed(self, symbol: str, exit_price: float, qty: int) -> None:
        """Record a closed position to self_learn DB (called after SELL execution)."""
        sig_id = self._signal_id_by_symbol.get(symbol)
        entry_price = self.entry_price_by_symbol.get(symbol, 0.0)
        entry_time = self._entry_time_by_symbol.get(symbol)
        if not sig_id or entry_price <= 0:
            return
        pnl = (exit_price - entry_price) * qty
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
        hold_minutes = 0
        if entry_time:
            hold_minutes = int((datetime.now(timezone.utc) - entry_time).total_seconds() / 60)
        try:
            from self_learn import on_trade_closed
            on_trade_closed(
                signal_id=sig_id,
                exit_price=float(exit_price),
                pnl=float(pnl),
                pnl_pct=float(pnl_pct),
                hold_minutes=hold_minutes,
            )
        except Exception:
            pass
        finally:
            self._signal_id_by_symbol.pop(symbol, None)
            self._entry_time_by_symbol.pop(symbol, None)

    # ── Strategy v3 Plan B: SHORT position management ──────────────────────────

    def _execute_short_entry(
        self,
        symbol: str,
        qty: int,
        price: float,
        reason: str,
        indicators: dict | None = None,
    ) -> None:
        """Enter a SHORT position: borrow & sell stock, expecting price to drop."""
        if not self.config.auto_trade:
            return
        reference_price = self.futu_connector.get_order_reference_price(symbol, "SELL", fallback_price=price)
        fill_price = reference_price if reference_price > 0 else price

        qty = self._round_to_lot(qty, symbol) if symbol.endswith(".HK") else qty
        if qty <= 0:
            self.logger.info("SKIP SHORT %s (lot rounding → qty=0)", symbol)
            return

        # Record SHORT position state
        self.short_position_qty_by_symbol[symbol] = qty
        self.short_entry_price_by_symbol[symbol] = fill_price
        self.lowest_price_since_short_by_symbol[symbol] = fill_price
        self.bars_held_short_by_symbol[symbol] = 0
        self.cycles_since_short_by_symbol[symbol] = 0
        self.buy_cover_signal_streak_by_symbol[symbol] = 0

        if self.config.paper_trading:
            self.logger.info("PAPER_SHORT %s qty=%d limit=%.4f type=NORMAL", symbol, qty, fill_price)
        else:
            self.futu_connector.place_order(symbol, qty, "SELL", fill_price)

        self.logger.info("EXEC_SHORT %s qty=%d fill=%.4f reason=%s", symbol, qty, fill_price, reason)

        # Log to trades.jsonl
        import os as _os
        import json as _json
        trade_log = _os.path.join(_os.path.dirname(__file__), "..", "..", "trades.jsonl")
        trade_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "side": "SHORT",
            "qty": qty,
            "price": float(fill_price),
            "reason": reason,
            "paper": bool(self.config.paper_trading),
        }
        try:
            with open(trade_log, "a", encoding="utf-8") as f:
                f.write(_json.dumps(trade_entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.logger.warning("Failed to log SHORT trade to JSONL: %s", exc)

        # Self-Learning: log SHORT signal
        try:
            from self_learn import hook_on_signal
            _sig_id = hook_on_signal(
                action="SHORT",
                prediction_id=self._short_pred_id_by_symbol.get(symbol),
                entry_price=float(fill_price),
                size=int(qty),
            )
            self._short_signal_id_by_symbol[symbol] = _sig_id
            self._short_entry_time_by_symbol[symbol] = datetime.now(timezone.utc)
        except Exception:
            pass

    def _execute_short_exit(
        self,
        symbol: str,
        qty: int,
        price: float,
        reason: str,
    ) -> None:
        """Cover (buy back) a SHORT position to close it."""
        if not self.config.auto_trade:
            return
        reference_price = self.futu_connector.get_order_reference_price(symbol, "BUY", fallback_price=price)
        fill_price = reference_price if reference_price > 0 else price

        qty = self._round_to_lot(qty, symbol) if symbol.endswith(".HK") else qty
        if qty <= 0:
            self.logger.info("SKIP SHORT COVER %s (lot rounding → qty=0)", symbol)
            return

        # Clear SHORT position state
        self.short_position_qty_by_symbol[symbol] = 0
        self.short_entry_price_by_symbol[symbol] = 0.0
        self.lowest_price_since_short_by_symbol[symbol] = 0.0
        self.bars_held_short_by_symbol[symbol] = 0
        self.cycles_since_short_by_symbol[symbol] = 999999
        self.buy_cover_signal_streak_by_symbol[symbol] = 0

        if self.config.paper_trading:
            self.logger.info("PAPER_SHORT_COVER %s qty=%d limit=%.4f type=NORMAL", symbol, qty, fill_price)
        else:
            self.futu_connector.place_order(symbol, qty, "BUY", fill_price)

        self.logger.info("EXEC_SHORT_COVER %s qty=%d fill=%.4f reason=%s", symbol, qty, fill_price, reason)

        # Log to trades.jsonl
        import os as _os
        import json as _json
        trade_log = _os.path.join(_os.path.dirname(__file__), "..", "..", "trades.jsonl")
        trade_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "side": "SHORT_COVER",
            "qty": qty,
            "price": float(fill_price),
            "reason": reason,
            "paper": bool(self.config.paper_trading),
        }
        try:
            with open(trade_log, "a", encoding="utf-8") as f:
                f.write(_json.dumps(trade_entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.logger.warning("Failed to log SHORT COVER trade to JSONL: %s", exc)

    def _record_short_closed(self, symbol: str, cover_price: float, qty: int) -> None:
        """Record a closed SHORT position to self_learn DB.

        SHORT PnL = entry_price - exit_price (price dropped = profit, price rose = loss).
        """
        sig_id = self._short_signal_id_by_symbol.get(symbol)
        entry_price = self.short_entry_price_by_symbol.get(symbol, 0.0)
        entry_time = self._short_entry_time_by_symbol.get(symbol)
        if not sig_id or entry_price <= 0:
            return
        # SHORT PnL: entry (higher) - cover (lower) = profit when price falls
        pnl = (entry_price - cover_price) * qty
        pnl_pct = ((entry_price - cover_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
        hold_minutes = 0
        if entry_time:
            hold_minutes = int((datetime.now(timezone.utc) - entry_time).total_seconds() / 60)
        try:
            from self_learn import on_trade_closed
            on_trade_closed(
                signal_id=sig_id,
                exit_price=float(cover_price),
                pnl=float(pnl),
                pnl_pct=float(pnl_pct),
                hold_minutes=hold_minutes,
            )
        except Exception:
            pass
        finally:
            self._short_signal_id_by_symbol.pop(symbol, None)
            self._short_entry_time_by_symbol.pop(symbol, None)

    def _sync_sentiment(self) -> None:
        import os as _os
        import json as _json
        sentiment_file = _os.path.join(_os.path.dirname(__file__), "..", "..", "sentiment.json")
        if _os.path.exists(sentiment_file):
            try:
                with open(sentiment_file, "r") as f:
                    data = _json.load(f)
                    self.sentiment_score = float(data.get("score", 0.0))
                    self.sentiment_summary = str(data.get("summary", "N/A"))
                    self.logger.info("Sentiment Synced: score=%.2f, summary=%s", self.sentiment_score, self.sentiment_summary)
            except Exception as exc:
                self.logger.warning("Sentiment sync failed: %s", exc)

    def _append_decision_trace(self, payload: dict) -> None:
        """Append Layer-0 decision trace for US SIM learning.

        This is intentionally append-only and best-effort (never crashes the trading loop).
        """
        if str(os.getenv("ENABLE_DECISION_TRACE", "1")).strip().lower() in {"0", "false", "no", "off"}:
            return

        try:
            # core/ -> v3_pipeline/ -> kiro-quant-v3/ -> workspace-quant/
            workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            out_dir = os.path.join(workspace_root, "learning", "us_sim")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, "decision_trace_us_sim.jsonl")

            now_utc = datetime.now(timezone.utc)
            # explicit HKT offset (+08:00)
            now_hkt = now_utc.astimezone(timezone(timedelta(hours=8)))

            event = {
                "schema": "kiro.us_sim.decision_trace.v1",
                "ts_utc": now_utc.isoformat(timespec="seconds"),
                "ts_hkt": now_hkt.isoformat(timespec="seconds"),
                "env": {
                    "no_futu": str(os.getenv("NO_FUTU", "")),
                    "futu_trade_only": str(os.getenv("FUTU_TRADE_ONLY", "")),
                },
                **payload,
            }

            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.logger.warning("decision_trace append failed: %s", exc)

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
