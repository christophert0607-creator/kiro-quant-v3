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
from v3_pipeline.core.market_context import build_market_contexts, sync_positions_to_contexts
from v3_pipeline.core.monte_carlo import MonteCarloSimulator
from v3_pipeline.core.strategy_factory import StrategyFactory
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator
from v3_pipeline.models.manager import DataPreparer, ModelManager
from v3_pipeline.risk.manager import RiskController


def _get_log_dir() -> Path:
    """Return the log directory, honouring KIRO_LOG_DIR env var for test isolation."""
    override = os.environ.get("KIRO_LOG_DIR")
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    default = Path(__file__).parent.parent.parent / "logs"
    default.mkdir(exist_ok=True)
    return default


def _build_structured_logger(name: str) -> logging.Logger:
    """Returns a logger that writes one JSON line per record to logs/decisions.jsonl."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        from logging.handlers import RotatingFileHandler
        log_dir = _get_log_dir()
        handler = RotatingFileHandler(
            log_dir / "decisions.jsonl",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    except Exception as exc:
        sys.stderr.write(f"[_build_structured_logger] RotatingFileHandler failed: {exc}\n")
    logger.propagate = False
    return logger


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
        log_dir = _get_log_dir()
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
    min_confidence_threshold: float = 0.10  # 2026-04-23: lowered from 0.20 to allow more momentum signals through

    # ---- Strategy v2: Entry gates ----
    rsi_oversold_entry: int = 50          # 2026-04-23: raised from 30 — RSI<40 is extreme, RSI<50 catches mean-reversion
    rsi_extreme_oversold: int = 35        # 2026-04-23: NEW — forced BUY without confidence gate (RSI < 35 is rare)
    bb_position_threshold: float = 0.2     # 2026-04-23: NEW — %B < 0.2 as auxiliary mean-reversion confirm
    vix_dynamic_confidence_enabled: bool = True  # 2026-04-23: NEW — VIX<18 → thresh-20%, VIX>25 → thresh+50%
    sma_filter: bool = True                # Price must be > SMA_20 for LONG
    macd_positive_required: bool = True   # MACD_HIST > 0 for oversold entry
    vix_max: float = 30.0                  # Block all entries if VIX >= 30
    sentiment_min_entry: float = -0.5      # 2026-04-15 Fix: Lowered from -0.1 to -0.5 to allow trades in current session

    # ---- Strategy v3: SHORT Entry gates (Plan B dual-directional) ----
    short_enabled: bool = True             # Enable SHORT (bearish/RSI overbought entries)
    rsi_overbought_entry: int = 70         # RSI > 70 for overbought SHORT entry
    rsi_deep_overbought: int = 80          # RSI > 80 for deep overbought (stronger conviction)
    macd_negative_required: bool = True    # MACD_HIST < 0 for SHORT entry
    sma_filter_short: bool = True           # Price must be < SMA_20 for SHORT
    short_min_confidence_threshold: float = 0.10  # 2026-04-23: lowered from 0.20 to align with long-side
    short_sentiment_max: float = 0.10      # Block SHORT if sentiment > 0.10 (too bullish)

    # ---- Strategy v2: Exit ----
    take_profit_rsi20: float = 0.03       # 3% TP when RSI < 20 (deep oversold)
    take_profit_normal: float = 0.02       # 2% TP for normal entry
    stop_loss: float = 0.015              # 1.5% SL (widened from 1%)
    trailing_stop_active: bool = True
    trailing_stop_trigger: float = 0.015   # Activate trailing after 1.5% profit
    trailing_stop_lock: float = 0.0        # Lock at entry price (0% trailing)
    min_hold_minutes: int = 45            # 2026-04-23: raised from 30 — INTC 33min time_exit was premature
    max_hold_minutes: int = 120           # Hard exit after 2 hours
    time_exit_near_entry_pct: float = 0.005  # 2026-04-23: raised from 0.003 — 0.3% too tight for low-vol

    # ---- Strategy v3: SHORT Exit (Plan B) ----
    short_stop_loss: float = 0.015         # 1.5% SL for SHORT positions (price rise = loss)
    short_take_profit: float = 0.02        # 2% TP for SHORT positions
    short_trailing_stop_trigger: float = 0.015  # Activate trailing after 1.5% profit (for SHORT)
    short_trailing_stop_lock: float = 0.0  # Lock at entry for SHORT

    # ---- check_and_trade bridge fields ----
    log_trade_decisions: bool = True
    swing_strategy_enabled: bool = True
    swing_rsi_oversold: float = 30.0
    swing_rsi_overbought: float = 70.0
    swing_sr_window: int = 20
    swing_sr_tolerance: float = 0.003
    bypass_ror_gate: bool = False
    diagnostics_verbose: bool = True
    quick_take_profit_pct: float = 0.01
    stop_loss_pct: float = 0.02
    max_hold_bars: int = 5
    max_position_fraction: float = 0.30
    min_diversified_symbols: int = 3
    max_diversified_symbols: int = 5
    pattern_confidence_threshold: float = 0.65
    pattern_threshold_relaxation: float = 0.25


class LiveTradingLoop:
    @property
    def telegram(self):
        class TelegramProxy:
            def __init__(self, loop):
                self.loop = loop
            async def send_message(self, text):
                from notifier import send_tg_msg
                await asyncio.to_thread(send_tg_msg, text)
        return TelegramProxy(self)
    def __init__(
        self,
        model_manager: ModelManager,
        risk_controller: RiskController,
        futu_connector: Optional[FutuConnector] = None,
        feature_generator: Optional[TechnicalIndicatorGenerator] = None,
        config: Optional[LiveConfig] = None,
        data_manager=None,
    ) -> None:
        self.model_manager = model_manager
        self.risk_controller = risk_controller
        self.futu_connector = futu_connector or FutuConnector()
        self.feature_generator = feature_generator or TechnicalIndicatorGenerator()
        self.config = config or LiveConfig()
        self.data_manager = data_manager
        self.broker_offline_fallback_mode: bool = False
        self.futu_reconnect_failures: int = 0
        self.latest_prices: dict[str, float] = {}
        self.logger = _build_stderr_logger(self.__class__.__name__)
        self.structured_logger = _build_structured_logger("kiro.decisions")

        # [V3-Dynamic-Screener] Priority: dynamic_watchlist.json
        watchlist_path = Path("dynamic_watchlist.json")
        symbols = []
        if watchlist_path.exists():
            try:
                import json
                with open(watchlist_path, "r") as f:
                    wdata = json.load(f)
                    if wdata.get("picks"):
                        symbols = wdata["picks"]
                        self.logger.info("[SCREENER] Today's top picks: %s", symbols)
            except Exception as e:
                self.logger.warning("Failed to load dynamic watchlist: %s", e)
        
        if not symbols:
            symbols = self.config.symbols_list or [self.config.symbol]
        self.symbols = symbols
        # Per-market context: partitions symbols into HK/US and tracks per-market account state.
        # The flat position dicts below remain the operational state; market_contexts provides
        # explicit isolation for account routing and cross-market sync operations.
        self.market_contexts = build_market_contexts(self.symbols)
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
        self.day_start_equity: float | None = None
        self.day_start_key: str | None = None

        # Macro Analysis results
        self.risk_mode = "neutral"
        self.risk_multiplier = 1.0

        # ── Self-Learning: track open signal IDs per symbol ──
        self._signal_id_by_symbol: dict[str, str] = {}
        self._short_signal_id_by_symbol: dict[str, str] = {}  # v3: SHORT signal IDs
        self._pred_id_by_symbol: dict[str, str | None] = {}
        self._short_pred_id_by_symbol: dict[str, str | None] = {}  # v3
        self._entry_pred_price_by_symbol: dict[str, float] = {}  # 2026-04-23: for prediction_error MAE
        self._entry_time_by_symbol: dict[str, datetime] = {}
        self._short_entry_time_by_symbol: dict[str, datetime] = {}  # v3
        self._short_entry_pred_price_by_symbol: dict[str, float] = {}  # 2026-04-23
        self._last_sell_time_by_symbol: dict[str, datetime] = {}  # 2026-04-24: prevent re-sync race
        self.account_value = 100000.0
        self.strategy_factory = StrategyFactory()
        self.alpha_engine = KiroAlphaEngine(AlphaConfig())
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

    def _current_trading_day_key(self) -> str:
        return datetime.now(timezone(timedelta(hours=8))).date().isoformat()

    def _refresh_daily_loss_anchor(self) -> None:
        today = self._current_trading_day_key()
        if self.day_start_key != today or self.day_start_equity is None or self.day_start_equity <= 0:
            self.day_start_key = today
            self.day_start_equity = max(0.0, float(self.account_value))
            self.logger.info(
                "[DAILY_LOSS_GATE] reset day_start_equity=%.2f day=%s",
                self.day_start_equity,
                self.day_start_key,
            )

    def _should_terminate_session(self) -> bool:
        from datetime import datetime, time as dt_time
        # Simplified market check (US hours HKT: 21:15-04:15)
        now = datetime.now()
        weekday = now.weekday()
        # Weekend check: Sat=5, Sun=6
        if weekday >= 5:
            return True  # Always terminate on weekend, let launcher re-evaluate
        cur_time = now.time()
        is_open = cur_time >= dt_time(21, 15) or cur_time <= dt_time(4, 15)
        if self.config.auto_trade and not is_open: return True
        if not self.config.auto_trade and is_open: return True
        return False

    async def _run_forever(self) -> None:
        primed = await self.history_primer.prime_symbols(self.symbols)
        for symbol, df in primed.items():
            if not df.empty:
                self.market_buffers[symbol] = self._normalize_market_buffer(df)
        self.logger.info("History priming completed for %d symbols", len(self.symbols))

        while True:
            if self._should_terminate_session():
                self.logger.info("Market session changed. Terminating loop for launcher to re-evaluate.")
                break
            await self.run_one_cycle()
            # [V3-Dynamic-Screener] Slow down polling in IDLE/sync mode
            sleep_sec = self.config.polling_seconds if self.config.auto_trade else 300
            await asyncio.sleep(sleep_sec)

    async def run_one_cycle(self) -> None:
        self._check_heartbeat()
        self._sync_broker_state()
        self._sync_sentiment()
        self.logger.info("[RUN_CYCLE] symbols=%d polling_sec=%d", len(self.symbols), self.config.polling_seconds)

        # ── Phase 1: Batch pre-fetch all quotes via cache ─────────────────────
        # All missing/stale quotes are fetched ONCE at cycle start.
        # Individual symbol cycles then hit cache (fast, zero network I/O).
        await self._prefetch_quotes()

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

    async def _prefetch_quotes(self) -> None:
        """
        Batch pre-fetch all quotes at the start of each cycle.

        Two-tier strategy:
        1. Alltick batch-kline (primary)  — fetches all symbols in batches of 10
           using the already-integrated alltick_connector. Rate limit: 1 req/10s.
           Cache TTL=30s means most cycles hit cache — zero network time.

        2. Per-symbol provider fallback (secondary) — only if Alltick fails.
           Uses FutuConnector.get_latest_quote() with the QuoteCache safety net.

        This replaces the previous pattern where each of the 59 symbol cycles
        independently called get_latest_quote(), causing 59 concurrent OpenD connections.
        """
        symbols = list(self.symbols)
        cache = self.futu_connector._quote_cache

        missing = cache.get_missing(symbols)
        if not missing:
            self.logger.info("[PREFETCH] All %d quotes fresh in cache — no fetch needed", len(symbols))
            return

        self.logger.info("[PREFETCH] %d/%d quotes stale, fetching now...", len(missing), len(symbols))
        t0 = time.perf_counter()
        fetched = 0

        remaining = list(missing)

        # ── Tier 1: iTick batch klines (US only) ───────────────────────────
        # iTick is a clean replacement for Alltick batch-kline when token is available.
        #
        # NOTE: iTick free tier is slow (rate-limited). To keep open-market cycles fast,
        # we optionally restrict iTick prefetch to a small allowlist and let the
        # per-symbol providers (YF/AV/Finnhub/ef) fill the rest.
        #
        # Env:
        # - USE_ITICK=0                              (set to 0 to disable iTick entirely)
        # - ITICK_PREFETCH_SYMBOLS="NVDA,TSLA,AAPL"  (comma-separated)
        # - ITICK_PREFETCH_MAX=3                     (fallback when list not set)
        us_missing = [s for s in remaining if not str(s).endswith(".HK")]

        use_itick = os.getenv("USE_ITICK", "1") != "0"
        itick_allow = os.getenv("ITICK_PREFETCH_SYMBOLS", "").strip()

        if use_itick and us_missing:
            if itick_allow:
                allow = {x.strip().upper() for x in itick_allow.split(",") if x.strip()}
                itick_targets = [s for s in us_missing if str(s).upper() in allow]
            else:
                try:
                    max_n = int(os.getenv("ITICK_PREFETCH_MAX", "3"))
                except Exception:
                    max_n = 3
                itick_targets = us_missing[: max(0, max_n)]

            if itick_targets:
                try:
                    self.logger.info(
                        "[PREFETCH] iTick targets=%d/%d (allow=%s)",
                        len(itick_targets),
                        len(us_missing),
                        itick_allow or f"max:{len(itick_targets)}",
                    )
                    itick_results = await asyncio.to_thread(
                        cache.refresh_batch_itick,
                        itick_targets,
                        "US",
                        1,
                        2,
                    )
                    fetched += len(itick_results)
                except Exception as exc:
                    self.logger.warning("[PREFETCH] iTick batch failed, falling back to per-symbol: %s", exc)

        remaining = cache.get_missing(symbols)
        if remaining:
            # ── Tier 2: per-symbol providers (futu/av/yf/ef) ───────────────
            try:
                results = await cache.refresh_batch_async(
                    remaining,
                    lambda s: self.futu_connector.get_latest_quote(s, use_cache=False),
                    max_concurrency=8,
                )
                fetched += len(results)
            except Exception as exc:
                self.logger.error("[PREFETCH] Per-symbol fetch failed: %s", exc)

        elapsed = time.perf_counter() - t0
        still_missing = len(cache.get_missing(symbols))
        self.logger.info(
            "[PREFETCH] Completed: %d/%d fetched in %.2fs, still missing: %d",
            fetched, len(missing), elapsed, still_missing
        )
        cache.log_stats()

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

        # ── Broker offline fallback: use data_manager when broker heartbeat failed ─
        if self.broker_offline_fallback_mode and self.data_manager is not None:
            try:
                dm_data = self.data_manager.get_market_data(symbol)
                price = float(dm_data.get("price", 0.0))
                source = dm_data.get("source", "UNKNOWN")
                quote = {
                    "Date": pd.Timestamp.now(tz="UTC"),
                    "Open": price, "High": price, "Low": price, "Close": price,
                    "Volume": 0.0, "data_source": source, "symbol": symbol,
                }
                self.logger.info("QuoteSource[%s] broker_online=False source=%s", symbol, source)
            except Exception as exc:
                self.logger.warning("DataManager fallback failed[%s]: %s", symbol, exc)
                return
            # Append to buffer and return; skip model predictions when broker is offline
            existing = self.market_buffers.get(symbol, pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume", "data_source"]))
            self.market_buffers[symbol] = pd.concat([existing, pd.DataFrame([quote])], ignore_index=True)
            return
        else:
            # ── QuoteCache lookup (primary) ───────────────────────────────────────────
            # QuoteCache TTL=30s: fresh enough for live trading, avoids redundant API calls.
            # Per-symbol provider (futu/yf/av) is only a fallback when cache misses.
            cache = getattr(self.futu_connector, "_quote_cache", None)
            quote = cache.get(symbol) if cache is not None else None
            if quote and quote.get("Close", 0) > 0:
                self.logger.info(
                    "[QUOTE_CACHE][%s] hit: price=%.4f age=%.1fs src=%s",
                    symbol,
                    quote.get("Close"),
                    (time.time() - quote.get("_cached_at", 0)),
                    quote.get("data_source", "?"),
                )
            else:
                # Cache miss — fetch directly from per-symbol provider with fallback chain
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

        if symbol not in self.data_preparers_by_symbol:
            self.data_preparers_by_symbol[symbol] = DataPreparer(
                lookback=self.model_manager.data_preparer.lookback,
                target_col=self.model_manager.data_preparer.target_col,
            )
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

        # ── Warm cache: use pre-computed prediction from IDLE if fresh (< 5 min) ─
        cached = getattr(self, "_idle_pred_cache", {}).get(symbol)
        now = time.time()
        if cached and (now - cached["ts"]) < 300:
            prediction = cached["pred"]
            confidence = cached["conf"]
            self.logger.info("[CACHE_HIT][%s] Using cached pred=%.4f conf=%.4f (age=%.0fs)", symbol, prediction, confidence, now - cached["ts"])
        else:
            prediction = float(self.model_manager.predict(wfa_frame, data_preparer=symbol_preparer))
            raw_ratio = abs(prediction - current_price) / max(current_price, 1e-9)

            # ── 2026-04-23 Fix: MAE-based confidence (replaces raw_ratio×50) ─────────
            # Fallback to raw_ratio only when no history is available yet.
            # Once prediction_error accumulates in outcomes DB, MAE-based conf dominates.
            try:
                from self_learn.models import get_prediction_accuracy
                mae, dir_acc = await asyncio.to_thread(get_prediction_accuracy, symbol, window=20)
            except Exception:
                mae, dir_acc = 0.0, 0.5  # default if DB unavailable

            if mae > 0 and current_price > 0:
                # New formula: accuracy = (1 - MAE/price) × directional_accuracy
                # Perfect prediction (MAE=0, dir_acc=1) → conf = 1.0
                # No history / uncertain → defaults to raw_ratio approach
                accuracy_score = max(0.0, 1.0 - mae / current_price) * dir_acc
                mae_confidence = min(1.0, accuracy_score)
                # Blend 60% MAE-based + 40% raw_ratio (keeps signal responsive)
                confidence = 0.6 * mae_confidence + 0.4 * min(1.0, raw_ratio * 50.0)
                self.logger.info(
                    "[CONF_NEW][%s] pred=%.4f price=%.4f raw_ratio=%.4f mae=%.4f dir_acc=%.2f → conf=%.4f",
                    symbol, prediction, current_price, raw_ratio, mae, dir_acc, confidence,
                )
            else:
                # No MAE history yet — fall back to raw deviation (legacy)
                confidence = min(1.0, raw_ratio * 50.0)

        self.logger.info(
            "[%s] Prediction (pred=%.4f, current=%.4f, change=%.2f%%)",
            symbol, prediction, current_price,
            (prediction - current_price) / max(current_price, 1e-9) * 100,
        )

        # Provide latest technical indicators snapshot for optional tactical entries
        latest_ind: dict[str, float] = {}
        try:
            latest_row = featured.iloc[-1]
            for k in ("RSI_14", "MACD_HIST", "SMA_5", "SMA_20", "BB_LOWER", "BB_MIDDLE", "BB_POSITION"):
                if k in latest_row.index:
                    v = latest_row.get(k)
                    if v is not None:
                        latest_ind[k] = float(v)
        except Exception:
            latest_ind = {}

        self._emit_structured(
            "model_predict",
            symbol=symbol,
            pred=round(float(prediction), 6),
            confidence=round(float(confidence), 4),
            price=round(float(current_price), 4),
            indicators={k: round(v, 4) for k, v in latest_ind.items()},
        )

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

        pattern_label: str = "Unknown"
        pattern_confidence: float = 0.0
        if hasattr(self.model_manager, "predict_pattern"):
            raw_pattern = self._normalize_pattern_meta(
                self.model_manager.predict_pattern(wfa_frame), symbol
            )
            if raw_pattern is not None:
                pattern_label = str(raw_pattern.get("pattern", "Unknown"))
                pattern_confidence = float(raw_pattern.get("confidence", 0.0))

        self.check_and_trade(
            symbol,
            current_price,
            prediction,
            confidence,
            profile.allow_long,
            latest_frame=featured,
            pattern_label=pattern_label,
            pattern_confidence=pattern_confidence,
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
        # ---- Macro Risk Filter ----
        if self.risk_mode == "risk_off":
            # Reduce position size or tighten stops if macro is bad
            risk_multiplier *= self.risk_multiplier  # reduce size
            self.logger.info(f"[MACRO_FILTER] Risk Off: applying multiplier {self.risk_multiplier}")
        if self.risk_controller.circuit_breaker_triggered(self.equity_peak, self.account_value):
            self._notify(f"🚨 Circuit breaker hit. Equity={self.account_value:.2f}")
            return

        # ---- Prepare thresholds (Move to top to avoid UnboundLocalError) ----
        bucket = str(self.config.bucket_by_symbol.get(symbol, "mid"))
        bucket_th = float(self.config.bucket_thresholds.get(bucket, self.config.prediction_threshold))
        # Priority: per-symbol override > bucket default > global default
        symbol_threshold = float(self.config.prediction_thresholds.get(symbol, bucket_th))
        threshold_up = current_price * (1 + symbol_threshold)
        threshold_down = current_price * (1 - symbol_threshold)

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
                tp_thresh = self.config.take_profit_rsi20 if entry_rsi <= self.config.rsi_extreme_oversold else self.config.take_profit_normal
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
            macd_ok = not self.config.macd_positive_required or macd_hist > (0.0 if symbol.endswith(".HK") else 0.0)
            trend_ok = not self.config.sma_filter or current_price >= sma20

            # ── 2026-04-24: Smart market-aware RSI threshold ──────────────────────
            market_adj = self._get_market_thresholds(symbol)
            rsi_entry_threshold = self.config.rsi_oversold_entry + market_adj["rsi_delta"]

            # ── 2026-04-23 Fix: RSI tier — extreme vs moderate ───────────────────────
            # Extreme oversold (RSI < 35): force BUY bypassing confidence gate
            rsi_extreme = int(getattr(self.config, 'rsi_extreme_oversold', 35))
            bb_pos = float(indicators.get("BB_POSITION", 0.5))
            bb_thresh = float(getattr(self.config, 'bb_position_threshold', 0.2))
            if rsi <= rsi_extreme and macd_ok and trend_ok:
                self.logger.info(
                    "[RSI_EXTREME_BUY][%s]: rsi=%.1f (<= %d) bb_pos=%.2f macd=%.4f px=%.4f — forced entry, no conf gate",
                    symbol, rsi, rsi_extreme, bb_pos, macd_hist, current_price,
                )
                buy_reason = "rsi_extreme_oversold"
                confidence = max(confidence, 0.60)
                prediction = current_price * 1.001
            # Moderate oversold (RSI < rsi_oversold_entry): standard entry with confidence gate
            # 2026-04-23: add %B < threshold as auxiliary confirm (BB touching lower band = mean-reversion)
            elif rsi <= rsi_entry_threshold and macd_ok and trend_ok and bb_pos <= bb_thresh:
                self.logger.info(
                    "[RSI_OVERSOLD_BUY][%s]: rsi=%.1f bb_pos=%.2f (<= %.2f) macd_hist=%.4f px=%.4f sma20=%.4f trend_ok=%s",
                    symbol, rsi, bb_pos, bb_thresh, macd_hist, current_price, sma20, trend_ok,
                )
                buy_reason = "rsi_oversold_v2"
                confidence = max(confidence, 0.50)
                prediction = current_price * 1.001

        # ── 2026-04-24: Smart market-aware thresholds (HK vs US) ───────────────
        market_adj = self._get_market_thresholds(symbol)
        effective_min_conf = float(getattr(self.config, 'min_confidence_threshold', 0.10)) * market_adj["conf_mult"]
        if getattr(self.config, 'vix_dynamic_confidence_enabled', False):
            try:
                vix_now = self._get_vix()  # synchronous; runs in-thread already via to_thread at call site
                if vix_now < 18:
                    effective_min_conf *= 0.80
                    self.logger.info("[VIX_DYNAMIC][%s] VIX=%.1f < 18 → conf thresh %.2f (×0.80)", symbol, vix_now, effective_min_conf)
                elif vix_now > 25:
                    effective_min_conf *= 1.50
                    self.logger.info("[VIX_DYNAMIC][%s] VIX=%.1f > 25 → conf thresh %.2f (×1.50)", symbol, vix_now, effective_min_conf)
            except Exception:
                pass

        # ── 2026-04-12 Fix: Confidence hard gate ──────────────────────────────────
        # Block if raw confidence is below threshold AND no RSI/MACD entry was triggered.
        # RSI/MACD entries set buy_reason themselves; absence means pure model signal.
        if allow_long and qty == 0:
            raw_conf_ok = confidence >= effective_min_conf
            is_rsiedge_entry = buy_reason in ("rsi_oversold_v2", "rsi_extreme_oversold", "tech_entry_v2")
            if not raw_conf_ok and not is_rsiedge_entry:
                self.logger.info(
                    "[CONF_GATE][%s] blocked: raw_conf=%.4f < %.2f (no RSI/MACD edge, %s adj)",
                    symbol, confidence, effective_min_conf, "HK" if symbol.endswith(".HK") else "US",
                )
                self._append_decision_trace({
                    **trace_base,
                    "action": "BUY_BLOCKED_LOW_CONF",
                    "raw_confidence": float(confidence),
                    "min_confidence": effective_min_conf,
                    "vix_dynamic": getattr(self.config, 'vix_dynamic_confidence_enabled', False),
                })
                self._emit_structured(
                    "risk_gate",
                    symbol=symbol,
                    gate="CONF_GATE_BUY",
                    raw_confidence=round(float(confidence), 4),
                    min_confidence=round(effective_min_conf, 4),
                )
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
            market_adj_short = self._get_market_thresholds(symbol)
            min_conf_short = float(getattr(self.config, 'short_min_confidence_threshold', 0.20)) * market_adj_short["short_conf_mult"]
            raw_conf_short_ok = confidence >= min_conf_short
            is_short_tactical = short_reason in ("rsi_overbought_short_v3",)
            if not raw_conf_short_ok and not is_short_tactical:
                self.logger.info(
                    "[SHORT_CONF_GATE][%s] blocked: raw_conf=%.4f < %.2f (no RSI-overbought edge, %s adj)",
                    symbol, confidence, min_conf_short, "HK" if symbol.endswith(".HK") else "US",
                )
                self._append_decision_trace({
                    **trace_base,
                    "action": "SHORT_BLOCKED_LOW_CONF",
                    "raw_confidence": float(confidence),
                    "min_confidence": min_conf_short,
                })
                self._emit_structured(
                    "risk_gate",
                    symbol=symbol,
                    gate="CONF_GATE_SHORT",
                    raw_confidence=round(float(confidence), 4),
                    min_confidence=round(min_conf_short, 4),
                )
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
                    total_positions = active_positions + active_short_positions
                    max_pos = int(self.config.max_portfolio_positions)
                    # v3: maintain 80% occupancy — close worst SHORT when >= 80% capacity
                    threshold_80 = int(max_pos * 0.8)
                    if total_positions >= max_pos:
                        self.logger.info("[%s] Max positions (%d) reached, skipping SHORT",
                            symbol, max_pos)
                    elif total_positions >= threshold_80:
                        # Find worst SHORT (lowest profit_pct) and close it
                        worst_sym = None
                        worst_profit_pct = float('inf')
                        for s, qty in self.short_position_qty_by_symbol.items():
                            if qty <= 0:
                                continue
                            entry_px = self.short_entry_price_by_symbol.get(s, 0.0)
                            curr_px = self.last_price_by_symbol.get(s, 0.0)
                            if entry_px <= 0 or curr_px <= 0:
                                continue
                            profit_pct = (entry_px - curr_px) / entry_px
                            if profit_pct < worst_profit_pct:
                                worst_profit_pct = profit_pct
                                worst_sym = s
                        if worst_sym:
                            self.logger.info(
                                "[80PCT_EVICT][%s] worst_SHORT=%s profit=%.2f%% — closing to make room",
                                symbol, worst_sym, worst_profit_pct * 100,
                            )
                            worst_qty = self.short_position_qty_by_symbol[worst_sym]
                            worst_curr = self.last_price_by_symbol.get(worst_sym, 0.0)
                            self._execute_short_exit(worst_sym, worst_qty, worst_curr, "80pct_occupancy_evict")
                            # Re-count after eviction
                            active_short_positions = sum(1 for q in self.short_position_qty_by_symbol.values() if q > 0)
                        # Proceed with new SHORT entry

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
                                self._execute_short_entry(symbol, short_alloc_qty, current_price, short_reason, indicators, prediction)
                            else:
                                self._append_decision_trace({
                                    **trace_base,
                                    "action": "SHORT_SKIPPED_QTY0",
                                    "alloc": float(alloc),
                                })
            # Return after processing SHORT to prevent LONG logic from also running
            return

        # ── Guard: Cannot BUY if short position exists ─────────────────────
        short_qty = int(self.short_position_qty_by_symbol.get(symbol, 0) or 0)
        if short_qty > 0:
            self.logger.info(
                "[FLIP_DETECTED][%s] Short position exists (%d). Triggering automatic cover before BUY.",
                symbol, short_qty,
            )
            # Trigger short exit to clear the way for a LONG position
            self._execute_short_exit(symbol, short_qty, current_price, "AUTO_FLIP_FOR_LONG")
            self._append_decision_trace({
                **trace_base,
                "action": "AUTO_COVER_FOR_LONG",
                "short_qty": short_qty,
            })
            return # Wait for next cycle to confirm cover and enter LONG

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
                self._execute(symbol, "BUY", buy_qty, current_price, buy_reason, indicators, prediction)

                # Self-Learning: log BUY signal
                try:
                    from self_learn import hook_on_signal
                    _sig_id = hook_on_signal(
                        action="BUY",
                        prediction_id=self._pred_id_by_symbol.get(symbol),
                        entry_price=float(current_price),
                        size=int(buy_qty),
                    )
                    self._signal_id_by_symbol[symbol] = _sig_id
                    if prediction is not None:
                        self._entry_pred_price_by_symbol[symbol] = float(prediction)
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

    # ── 2026-04-24: Smart market-aware thresholds ───────────────────────────
    def _get_market_thresholds(self, symbol: str) -> dict:
        """智能分辨港美市場，動態調整門檻。"""
        if symbol.endswith(".HK"):
            # 港股：基於數據質量較差，自動降低門檻
            return {
                "conf_mult": 0.40,
                "rsi_delta": -10,
                "macd_mult": 0.33,
                "short_conf_mult": 0.50,
            }

        # 美股：保持原有嚴格標準
        return {
            "conf_mult": 1.0,
            "rsi_delta": 0,
            "macd_mult": 1.0,
            "short_conf_mult": 1.0,
        }

    # ── End smart thresholds ──────────────────────────────────────────────────

    def _round_to_lot(self, qty: int, symbol: str) -> int:
        if symbol.endswith(".HK"):
            # 2026-04-15 Fix: Unlock HK trading with standard 100-share lot size
            lot_size = 100
            rounded = (qty // lot_size) * lot_size
            if rounded < lot_size:
                # self.logger.info("SKIP HK %s: qty %d below lot size %d", symbol, qty, lot_size)
                return 0
            return int(rounded)
        return int(qty)  # US stocks: lot_size=1

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

    def _normalize_pattern_meta(self, raw, symbol: str) -> dict | None:
        if not isinstance(raw, dict):
            self.logger.warning("predict_pattern returned non-dict for %s: %r", symbol, raw)
            return None
        try:
            float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            self.logger.warning(
                "predict_pattern confidence is invalid for %s: %r", symbol, raw.get("confidence")
            )
            return None
        return raw

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
        self._run_trading_logic_bridge(
            symbol,
            current_price,
            prediction,
            confidence,
            allow_long,
            latest_frame=latest_frame,
            pattern_label=pattern_label,
            pattern_confidence=pattern_confidence,
        )

    def _run_trading_logic_bridge(
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
            self._notify(f"Circuit breaker hit. Equity={self.account_value:.2f}")
            return

        qty_raw = int(self.position_qty_by_symbol.get(symbol, 0))
        qty = max(0, qty_raw)
        if qty_raw < 0:
            self.logger.warning("DIAG_QTY[%s] invalid negative qty=%d; clamped to 0", symbol, qty_raw)
        self.bars_held_by_symbol[symbol] = self.bars_held_by_symbol.get(symbol, 0) + (1 if qty > 0 else 0)
        if qty > 0:
            self.cycles_since_buy_by_symbol[symbol] = self.cycles_since_buy_by_symbol.get(symbol, 0) + 1

        if qty > 0:
            self.highest_price_since_entry_by_symbol[symbol] = max(
                self.highest_price_since_entry_by_symbol.get(symbol, 0.0), current_price
            )
            entry_price = float(self.entry_price_by_symbol.get(symbol, 0.0) or 0.0)
            if entry_price <= 0:
                entry_price = current_price
                self.entry_price_by_symbol[symbol] = entry_price

            stop_loss_pct = max(0.0, float(getattr(self.config, "stop_loss_pct", 0.02)))
            if stop_loss_pct > 0:
                stop_loss_price = entry_price * (1 - stop_loss_pct)
                if current_price <= stop_loss_price:
                    self._execute(symbol, "SELL", qty, current_price, f"stop_loss_{stop_loss_pct:.4f}")
                    return

            quick_tp_pct = max(0.0, float(getattr(self.config, "quick_take_profit_pct", 0.01)))
            take_profit_price = entry_price * (1 + quick_tp_pct)
            if current_price >= take_profit_price:
                self._execute(symbol, "SELL", qty, current_price, f"quick_take_profit_{quick_tp_pct:.4f}")
                return

            max_hold_bars = max(1, int(getattr(self.config, "max_hold_bars", 5)))
            if self.bars_held_by_symbol.get(symbol, 0) >= max_hold_bars:
                self._execute(symbol, "SELL", qty, current_price, f"max_hold_{max_hold_bars}_bars")
                return

        symbol_threshold = float(self.config.prediction_thresholds.get(symbol, self.config.prediction_threshold))
        pat_conf_threshold = float(getattr(self.config, "pattern_confidence_threshold", 0.65))
        pat_relax = float(getattr(self.config, "pattern_threshold_relaxation", 0.25))
        if pattern_confidence >= pat_conf_threshold and pattern_label in {"DoubleBottom", "UpTrend", "Reversal"}:
            symbol_threshold = symbol_threshold * max(0.5, 1.0 - pat_relax)

        threshold_up = current_price * (1 + symbol_threshold)
        threshold_down = current_price * (1 - symbol_threshold)
        predicted_move = (prediction - current_price) / max(current_price, 1e-9)
        model_buy_signal = predicted_move > symbol_threshold
        model_sell_signal = predicted_move < -symbol_threshold
        swing = self._evaluate_swing_signal(symbol, current_price, latest_frame)

        if getattr(self.config, "log_trade_decisions", True):
            self.logger.info(
                "TRADE_CHECK[%s] allow_long=%s qty=%d pred=%.4f px=%.4f move=%.2f%% up=%.4f down=%.4f conf=%.3f pattern=%s p_conf=%.2f",
                symbol, allow_long, qty, prediction, current_price, predicted_move * 100,
                threshold_up, threshold_down, confidence, pattern_label, pattern_confidence,
            )
            if getattr(self.config, "diagnostics_verbose", True):
                self.logger.info(
                    "DIAG_GATE[%s] allow_long=%s qty=%d swing_buy=%s swing_sell=%s model_buy=%s model_sell=%s bypass_ror=%s",
                    symbol, allow_long, qty, swing["buy_signal"], swing["sell_signal"],
                    model_buy_signal, model_sell_signal, getattr(self.config, "bypass_ror_gate", False),
                )

        if not allow_long and model_buy_signal and qty == 0:
            self.logger.info(
                "PROFILE_GATE[%s] blocked BUY because profile disallows long exposure (pred_move=%.2f%%)",
                symbol, predicted_move * 100,
            )
            return

        if getattr(self.config, "swing_strategy_enabled", True):
            if qty == 0 and allow_long and swing["buy_signal"]:
                risk_pct = self.strategy_factory.confidence_to_risk_pct(confidence)
                cap_fraction = max(0.0, min(1.0, float(getattr(self.config, "max_position_fraction", 0.30))))
                alloc = min(self.account_value * risk_pct, self.account_value * cap_fraction)
                buy_qty = max(0, int(alloc / max(current_price, 1e-9)))
                if buy_qty > 0:
                    self._execute(symbol, "BUY", buy_qty, current_price, f"swing_signal_conf={confidence:.3f}")
                    return
            elif qty > 0 and swing["sell_signal"]:
                self._execute(symbol, "SELL", qty, current_price, "swing_signal")
                return

        if allow_long and model_buy_signal and qty == 0:
            # position cap gate
            max_pos = getattr(self.config, "max_positions", getattr(self.config, "max_portfolio_positions", 999))
            open_positions = sum(1 for s, q in self.position_qty_by_symbol.items() if q > 0)
            if open_positions >= max_pos:
                return

            # daily loss gate
            self._refresh_daily_loss_anchor()
            if hasattr(self.risk_controller, "allow_daily_loss") and not self.risk_controller.allow_daily_loss(
                day_start_equity=float(self.day_start_equity or self.account_value),
                current_equity=float(self.account_value),
            ):
                self.logger.warning("[DAILY_LOSS_GATE][%s] blocked BUY: daily loss limit reached", symbol)
                return

            returns = self.market_buffers[symbol]["Close"].pct_change().dropna()
            mc = self.monte_carlo.stress_test(returns)
            risk_pct = self.strategy_factory.confidence_to_risk_pct(confidence)
            rr = max(0.5, abs(prediction - current_price) / max(current_price * symbol_threshold, 1e-6))
            bypass_ror = getattr(self.config, "bypass_ror_gate", False)
            if bypass_ror:
                self.logger.warning("[ROR_GATE][%s] bypass enabled for diagnostics", symbol)
            elif not self.risk_controller.allow_trade_with_ror(
                win_rate=mc["win_rate"],
                reward_risk_ratio=rr,
                risk_fraction=risk_pct,
                mc_var_95=mc["var95"],
            ):
                self.logger.warning(
                    "[ROR_GATE][%s] blocked BUY: win_rate=%.3f var95=%.4f",
                    symbol, mc["win_rate"], mc["var95"],
                )
                return

            cap_fraction = max(0.0, min(1.0, float(getattr(self.config, "max_position_fraction", 0.30))))
            alloc = min(self.account_value * risk_pct, self.account_value * cap_fraction)
            buy_qty = max(0, int(alloc / max(current_price, 1e-9)))
            if buy_qty > 0:
                self._execute(symbol, "BUY", buy_qty, current_price, f"model_signal_conf={confidence:.3f}")
        elif model_sell_signal and qty > 0:
            if self.cycles_since_buy_by_symbol.get(symbol, 0) >= self.config.buy_cooldown_cycles:
                self._execute(symbol, "SELL", qty, current_price, "model_signal")

    def _evaluate_swing_signal(
        self, symbol: str, current_price: float, latest_frame: Optional[pd.DataFrame]
    ) -> dict:
        frame = latest_frame if latest_frame is not None and not latest_frame.empty else self.market_buffers.get(symbol, pd.DataFrame())
        if frame.empty:
            return {
                "buy_signal": False, "sell_signal": False, "rsi": 50.0,
                "support": current_price, "resistance": current_price,
                "near_support": False, "near_resistance": False,
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

        window = max(2, int(getattr(self.config, "swing_sr_window", 20)))
        lows = pd.to_numeric(frame.get("Low") if hasattr(frame, "get") else frame["Low"] if "Low" in frame.columns else pd.Series(dtype=float), errors="coerce")
        highs = pd.to_numeric(frame.get("High") if hasattr(frame, "get") else frame["High"] if "High" in frame.columns else pd.Series(dtype=float), errors="coerce")
        support = float(lows.tail(window).min()) if not lows.empty and lows.tail(window).notna().any() else current_price
        resistance = float(highs.tail(window).max()) if not highs.empty and highs.tail(window).notna().any() else current_price
        tolerance = max(0.0, float(getattr(self.config, "swing_sr_tolerance", 0.003)))
        near_support = current_price <= support * (1 + tolerance)
        near_resistance = current_price >= resistance * (1 - tolerance)

        rsi_oversold = float(getattr(self.config, "swing_rsi_oversold", 30.0))
        rsi_overbought = float(getattr(self.config, "swing_rsi_overbought", 70.0))
        buy_signal = rsi_value < rsi_oversold or (near_support and macd_cross_up)
        sell_signal = rsi_value > rsi_overbought or (near_resistance and macd_cross_down)
        return {
            "buy_signal": bool(buy_signal), "sell_signal": bool(sell_signal),
            "rsi": rsi_value, "support": support, "resistance": resistance,
            "near_support": bool(near_support), "near_resistance": bool(near_resistance),
        }

    _MAX_RECONNECT_FAILURES = 3

    def _attempt_reconnect(self) -> None:
        if self.futu_reconnect_failures >= self._MAX_RECONNECT_FAILURES:
            return
        try:
            self.futu_connector.reconnect()
            self.futu_reconnect_failures = 0
            self.broker_offline_fallback_mode = False
        except Exception as exc:
            self.futu_reconnect_failures += 1
            self.logger.warning(
                "Reconnect failed (%d/%d): %s",
                self.futu_reconnect_failures,
                self._MAX_RECONNECT_FAILURES,
                exc,
            )
            if self.futu_reconnect_failures >= self._MAX_RECONNECT_FAILURES:
                self.broker_offline_fallback_mode = True
                self.logger.warning("Reconnect budget exhausted, broker_offline_fallback_mode=True")

    def _check_heartbeat(self) -> None:
        try:
            ok = self.futu_connector.heartbeat()
            if not ok:
                self.logger.warning("Broker heartbeat unhealthy")
                self.broker_offline_fallback_mode = True
            else:
                self.broker_offline_fallback_mode = False
                self.futu_reconnect_failures = 0
        except Exception as exc:
            self.logger.warning("Heartbeat check failed: %s", exc)
            self.broker_offline_fallback_mode = True

    def _sync_broker_state(self) -> None:
        import os as _os
        import json as _json
        workspace_root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", ".."))
        state_file = _os.path.join(workspace_root, "state.json")

        # 1. Sync total account value across all active market accounts.
        # When both HK and US trade contexts are live, sum assets from each so that
        # position sizing reflects the true combined buying power.
        try:
            if hasattr(self.futu_connector, "get_sync_assets_all_markets") and self.futu_connector.trade_ctxs:
                assets = self.futu_connector.get_sync_assets_all_markets()
            else:
                assets = self.futu_connector.get_sync_assets()
            total_assets = float(assets.get("total_assets", self.account_value))
            if total_assets > 0:
                self.account_value = total_assets
                self._refresh_daily_loss_anchor()
            else:
                # Broker returned 0 (SIMULATE account unfunded or transient connection issue).
                # Preserve the last known value to prevent a false 100% drawdown from
                # triggering the circuit breaker and blocking all trade execution.
                self._emit_structured(
                    "broker_sync_zero_equity",
                    kept_value=round(self.account_value, 2),
                    reason="broker returned total_assets=0; preserving last known value",
                )
        except Exception as exc:
            self.logger.warning("Asset sync failed: %s", exc)

        # ── Always sync from Futu broker (source of truth for ALL modes) ─────────────
        # This includes SIM mode: Futu's SIM account IS the source of truth for positions.
        # state.json is used ONLY as fallback if broker returns empty.
        try:
            # Use all-markets sync when available so HK + US positions are both captured
            if hasattr(self.futu_connector, "get_sync_positions_all_markets"):
                positions = self.futu_connector.get_sync_positions_all_markets()
            else:
                positions = self.futu_connector.get_sync_positions()
            # Reset all to zero before syncing
            for symbol in self.symbols:
                self.position_qty_by_symbol[symbol] = 0
                self.short_position_qty_by_symbol[symbol] = 0

            if not positions.empty and "code" in positions.columns:
                for _, row in positions.iterrows():
                    code = str(row.get("code", ""))
                    # Match to our symbol list
                    matched_symbol = None
                    for sym in self.symbols:
                        broker_code, _ = self.futu_connector.resolve_symbol(sym)
                        if broker_code == code:
                            matched_symbol = sym
                            break
                    if matched_symbol is None:
                        continue

                    qty = int(float(row.get("qty", 0)))
                    side = str(row.get("position_side", "LONG")).upper()

                    if qty == 0:
                        continue

                    # 2026-04-24 Fix: Skip sync if we just sold this symbol (race condition)
                    last_sell = self._last_sell_time_by_symbol.get(matched_symbol)
                    if last_sell is not None:
                        seconds_since_sell = (datetime.now(timezone.utc) - last_sell).total_seconds()
                        if seconds_since_sell < 10:  # 10-second cooldown
                            self.logger.info(
                                "Position sync [SKIP][%s]: sold %ds ago, using local state=%d",
                                matched_symbol, int(seconds_since_sell),
                                self.position_qty_by_symbol.get(matched_symbol, 0)
                            )
                            continue

                    if side == "LONG":
                        self.position_qty_by_symbol[matched_symbol] = max(0, qty)
                        self.logger.info("Position sync [BROKER][LONG]: %s = %d", matched_symbol, qty)
                    elif side == "SHORT":
                        self.short_position_qty_by_symbol[matched_symbol] = max(0, abs(qty))
                        self.logger.info("Position sync [BROKER][SHORT]: %s = %d", matched_symbol, abs(qty))
            else:
                self.logger.warning("Broker returned empty positions DataFrame")
        except Exception as exc:
            self.logger.warning("Broker position sync failed: %s — falling back to state.json", exc)
            # Fallback: try state.json
            if _os.path.exists(state_file):
                try:
                    with open(state_file, "r") as f:
                        state = _json.load(f)
                    positions_map = state.get("positions", {})
                    for symbol in self.symbols:
                        for key, qty in positions_map.items():
                            norm = key.replace("US.", "").replace(".HK", "HK")
                            if symbol.replace(".HK", "HK") == norm or symbol == key:
                                self.position_qty_by_symbol[symbol] = max(0, int(qty))
                                break
                    short_positions_map = state.get("short_positions", {})
                    for symbol in self.symbols:
                        for key, sqty in short_positions_map.items():
                            norm = key.replace("US.", "").replace(".HK", "HK")
                            if symbol.replace(".HK", "HK") == norm or symbol == key:
                                self.short_position_qty_by_symbol[symbol] = max(0, int(sqty))
                                break
                    self.logger.info("Fallback: positions synced from state.json")
                except Exception:
                    pass

        # Sync positions into per-market MarketContext objects, then reverse-sync
        # MarketContext back to flat dicts so MarketContext is the canonical view.
        try:
            broker_rows = [
                {"code": self.futu_connector.resolve_symbol(s)[0], "qty": q}
                for s, q in self.position_qty_by_symbol.items()
                if q != 0
            ]
            broker_rows += [
                {"code": self.futu_connector.resolve_symbol(s)[0], "qty": -q}
                for s, q in self.short_position_qty_by_symbol.items()
                if q > 0
            ]
            sync_positions_to_contexts(self.market_contexts, broker_rows)
            # Reverse-sync: make flat dicts consistent with MarketContext state.
            for _mctx in self.market_contexts.values():
                for _sym, _qty in _mctx.position_qty.items():
                    self.position_qty_by_symbol[_sym] = _qty
                for _sym, _qty in _mctx.short_position_qty.items():
                    self.short_position_qty_by_symbol[_sym] = _qty
        except Exception as exc:
            self.logger.debug("market_contexts sync skipped: %s", exc)

        active_positions = {s: q for s, q in self.position_qty_by_symbol.items() if q > 0}
        active_markets = list(self.market_contexts.keys())

        # Include account routing snapshot in broker_sync event (no secrets)
        account_snapshot: dict = {}
        if hasattr(self.futu_connector, "account_mapping_snapshot"):
            try:
                account_snapshot = self.futu_connector.account_mapping_snapshot()
            except Exception:
                pass

        self._emit_structured(
            "broker_sync",
            positions=active_positions,
            account_value=round(self.account_value, 2),
            market=getattr(self.futu_connector, "market_prefix", "unknown"),
            active_markets=active_markets,
            **account_snapshot,
        )

    def _execute(self, symbol: str, side: str, qty: int, price: float, reason: str, indicators: dict | None = None, prediction: float | None = None) -> None:
        # Guard: 所有副作用（position mutation + 下單）必須喺同一個 auto_trade block 內
        if not self.config.auto_trade:
            return

        # 2026-04-24 Fix: Double-check broker position before placing order.
        # Use all-markets positions so HK sell pre-checks are not missed when
        # primary trade_ctx is US.
        if side == "SELL" and not self.config.paper_trading:
            try:
                if hasattr(self.futu_connector, "get_sync_positions_all_markets"):
                    broker_positions = self.futu_connector.get_sync_positions_all_markets()
                else:
                    broker_positions = self.futu_connector.get_sync_positions()
                if not broker_positions.empty and "code" in broker_positions.columns:
                    broker_code, _ = self.futu_connector.resolve_symbol(symbol)
                    actual_qty = broker_positions[broker_positions["code"] == broker_code]["qty"].sum()
                    if actual_qty < qty:
                        self.logger.warning(
                            "[PRE_CHECK][%s] Broker qty=%d < requested=%d — skipping",
                            symbol, actual_qty, qty
                        )
                        return
            except Exception as exc:
                self.logger.warning("Pre-check position query failed: %s — proceeding with caution", exc)

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
            # ── Pre-execution guard: Check for existing short position ───────────────
            short_qty = int(self.short_position_qty_by_symbol.get(symbol, 0) or 0)
            if short_qty > 0:
                self.logger.warning(
                    "[EXEC_BLOCK][%s] BUY blocked: %d short shares outstanding. Cover short first.",
                    symbol, short_qty,
                )
                return

            self.position_qty_by_symbol[symbol] += qty
            self.highest_price_since_entry_by_symbol[symbol] = fill_price
            self.entry_price_by_symbol[symbol] = fill_price
            self.cycles_since_buy_by_symbol[symbol] = 0
            # v2: Store entry RSI for RSI-gated take profit at exit
            if indicators:
                self.entry_rsi_by_symbol[symbol] = float(indicators.get("RSI_14", 50.0))
        if side == "SELL":
            self.position_qty_by_symbol[symbol] = max(0, self.position_qty_by_symbol[symbol] - qty)
            if self.position_qty_by_symbol[symbol] == 0:
                self.highest_price_since_entry_by_symbol[symbol] = 0.0
                self.bars_held_by_symbol[symbol] = 0
                self.cycles_since_buy_by_symbol[symbol] = 999999
                # 2026-04-24 Fix: Mark last sell time to prevent immediate re-sync
                self._last_sell_time_by_symbol[symbol] = datetime.now(timezone.utc)

        if self.config.paper_trading:
            self.logger.info("PAPER_ORDER %s %s qty=%d limit=%.4f type=NORMAL", symbol, side, qty, fill_price)
        else:
            try:
                self.futu_connector.place_order(symbol, qty, side, fill_price)
            except Exception as exc:
                self.logger.error("[%s] Order failed: %s — reverting local state", symbol, exc)
                # Revert local position state on failure
                if side == "BUY":
                    self.position_qty_by_symbol[symbol] = max(0, self.position_qty_by_symbol.get(symbol, 0) - qty)
                else:
                    self.position_qty_by_symbol[symbol] += qty
                self._append_decision_trace({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol,
                    "action": "ORDER_FAILED_REVERTED",
                    "side": side,
                    "qty": qty,
                    "error": str(exc),
                })
                return  # Graceful exit — do not crash

        self.logger.info("EXEC %s %s qty=%d fill=%.4f reason=%s", symbol, side, qty, fill_price, reason)
        from v3_pipeline.core.market_context import infer_market as _infer_market
        _order_market = _infer_market(symbol)
        _order_account_id: int | None = None
        if hasattr(self.futu_connector, "_account_kwargs_for_market"):
            _order_account_id = self.futu_connector._account_kwargs_for_market(_order_market).get("acc_id")
        self._emit_structured(
            "order_attempt",
            symbol=symbol,
            side=side,
            qty=qty,
            price=round(float(fill_price), 4),
            reason=reason,
            paper=bool(self.config.paper_trading),
            market=_order_market,
            account_id=_order_account_id,
        )

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

            # ── Also persist SHORT positions ──────────────────────────────────────
            short_positions = state.setdefault("short_positions", {})
            short_positions[code] = int(self.short_position_qty_by_symbol.get(symbol, 0))

            last_orders = state.setdefault("last_orders", {})
            last_orders[code] = {
                "time": time.time(),
                "signal": side,
                "qty": int(qty),
                "price": float(fill_price),
                "reason": reason,
            }

            # Persist current account routing snapshot (no secrets) for dashboard visibility
            if hasattr(self.futu_connector, "account_mapping_snapshot"):
                try:
                    state["account_routing"] = self.futu_connector.account_mapping_snapshot()
                except Exception:
                    pass

            with open(state_file, "w", encoding="utf-8") as f:
                f.write(_json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        except Exception as exc:
            self.logger.warning("Failed to persist state.json: %s", exc)

    # ── Self-Learning: record closed position outcome ──────────────────────────
    def _record_trade_closed(self, symbol: str, exit_price: float, qty: int) -> None:
        """Record a closed position to self_learn DB (called after SELL execution)."""
        sig_id = self._signal_id_by_symbol.get(symbol)
        entry_price = self.entry_price_by_symbol.get(symbol, 0.0)
        entry_pred_price = self._entry_pred_price_by_symbol.get(symbol, 0.0)
        entry_time = self._entry_time_by_symbol.get(symbol)
        if not sig_id or entry_price <= 0:
            return
        pnl = (exit_price - entry_price) * qty
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
        hold_minutes = 0
        if entry_time:
            hold_minutes = int((datetime.now(timezone.utc) - entry_time).total_seconds() / 60)
        # 2026-04-23: prediction_error = |predicted_price - exit_price| for rolling MAE
        prediction_error = abs(entry_pred_price - exit_price) if entry_pred_price > 0 else None
        try:
            from self_learn import on_trade_closed
            on_trade_closed(
                signal_id=sig_id,
                exit_price=float(exit_price),
                pnl=float(pnl),
                pnl_pct=float(pnl_pct),
                hold_minutes=hold_minutes,
                prediction_error=prediction_error,
            )
        except Exception:
            pass
        finally:
            self._signal_id_by_symbol.pop(symbol, None)
            self._entry_pred_price_by_symbol.pop(symbol, None)
            self._entry_time_by_symbol.pop(symbol, None)

    # ── Strategy v3 Plan B: SHORT position management ──────────────────────────

    def _execute_short_entry(
        self,
        symbol: str,
        qty: int,
        price: float,
        reason: str,
        indicators: dict | None = None,
        prediction: float | None = None,
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
            if prediction is not None:
                self._short_entry_pred_price_by_symbol[symbol] = float(prediction)
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
        entry_pred_price = self._short_entry_pred_price_by_symbol.get(symbol, 0.0)
        entry_time = self._short_entry_time_by_symbol.get(symbol)
        if not sig_id or entry_price <= 0:
            return
        # SHORT PnL: entry (higher) - cover (lower) = profit when price falls
        pnl = (entry_price - cover_price) * qty
        pnl_pct = ((entry_price - cover_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
        hold_minutes = 0
        if entry_time:
            hold_minutes = int((datetime.now(timezone.utc) - entry_time).total_seconds() / 60)
        prediction_error = abs(entry_pred_price - cover_price) if entry_pred_price > 0 else None
        try:
            from self_learn import on_trade_closed
            on_trade_closed(
                signal_id=sig_id,
                exit_price=float(cover_price),
                pnl=float(pnl),
                pnl_pct=float(pnl_pct),
                hold_minutes=hold_minutes,
                prediction_error=prediction_error,
            )
        except Exception:
            pass
        finally:
            self._short_signal_id_by_symbol.pop(symbol, None)
            self._short_entry_pred_price_by_symbol.pop(symbol, None)
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

    def _emit_structured(self, event_type: str, **fields) -> None:
        """Emit one structured JSON event to logs/decisions.jsonl (best-effort, never raises)."""
        try:
            record = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": event_type,
                **fields,
            }
            self.structured_logger.info(json.dumps(record, ensure_ascii=False))
        except Exception:
            pass

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
