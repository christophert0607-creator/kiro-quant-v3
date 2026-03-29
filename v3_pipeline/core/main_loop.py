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
from v3_pipeline.core.alpha_engine import KiroAlphaEngine
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

    # Capital allocation buckets (fractions should sum to 1.0)
    bucket_fractions: dict[str, float] = field(
        default_factory=lambda: {"long": 0.5, "mid": 0.3, "short": 0.1, "reserve": 0.1}
    )
    bucket_by_symbol: dict[str, str] = field(default_factory=dict)

    # Per-bucket model trigger thresholds (fractional; e.g. 0.002 = 0.2%)
    bucket_thresholds: dict[str, float] = field(
        default_factory=lambda: {"long": 0.0020, "mid": 0.0015, "short": 0.0012, "avoid": 0.01}
    )

    max_portfolio_positions: int = 8


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
        self.cycles_since_buy_by_symbol: dict[str, int] = {s: 999999 for s in self.symbols}
        # Sell confirmation streak (avoid whipsaw exits when sentiment is bullish)
        self.sell_signal_streak_by_symbol: dict[str, int] = {s: 0 for s in self.symbols}
        self.entry_price_by_symbol: dict[str, float] = {s: 0.0 for s in self.symbols}
        self.last_price_by_symbol: dict[str, float] = {}
        self.profile_timings: list[dict] = []
        self.sentiment_score = 0.0
        self.sentiment_summary = "N/A"

        self.equity_peak = 0.0
        self.account_value = 100000.0
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
        prediction = float(self.model_manager.predict(wfa_frame, data_preparer=symbol_preparer))
        confidence = min(1.0, abs(prediction - current_price) / max(current_price, 1e-9))
        vix_value = await asyncio.to_thread(self._get_vix)
        profile = self.strategy_factory.choose_profile(vix_value, self.sentiment_score)

        self._detect_critical_move(symbol, current_price)

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

        self._run_trading_logic(
            symbol,
            current_price,
            prediction,
            confidence,
            profile.allow_long,
            profile.risk_multiplier,
            latest_ind,
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
    ) -> None:
        self.equity_peak = max(self.equity_peak, self.account_value)
        if self.risk_controller.circuit_breaker_triggered(self.equity_peak, self.account_value):
            self._notify(f"🚨 Circuit breaker hit. Equity={self.account_value:.2f}")
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
                if profit_pct >= 0.01:
                    self.logger.info("[%s] Take profit triggered! profit=%.2f%%", symbol, profit_pct * 100)
                    self._execute(symbol, "SELL", qty, current_price, "take_profit_1pct")
                    return
                elif profit_pct <= -0.02:
                    self.logger.info("[%s] Stop loss triggered! loss=%.2f%%", symbol, profit_pct * 100)
                    self._execute(symbol, "SELL", qty, current_price, "stop_loss_2pct")
                    return

            self.highest_price_since_entry_by_symbol[symbol] = max(self.highest_price_since_entry_by_symbol.get(symbol, 0.0), current_price)
            stop_price = self.highest_price_since_entry_by_symbol[symbol] * (1 - stop_pct)
            if current_price < stop_price:
                self._execute(symbol, "SELL", qty, current_price, "time_decay_vol_stop")
                return

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

        # Tactical entry for short bucket to increase trade frequency (mean-reversion / momentum turn)
        # Only when sentiment is not bearish (>= -0.2)
        if (
            qty == 0
            and allow_long
            and bucket == "short"
            and float(self.sentiment_score) >= -0.20
            and indicators
        ):
            rsi = float(indicators.get("RSI_14", 50.0))
            macd_hist = float(indicators.get("MACD_HIST", 0.0))
            sma5 = float(indicators.get("SMA_5", current_price))
            bb_lower = float(indicators.get("BB_LOWER", current_price))

            tech_buy = (rsi <= 35.0) and (macd_hist > 0.0) and (current_price >= sma5) and (current_price <= bb_lower * 1.01)
            if tech_buy:
                self.logger.info(
                    "TECH_BUY[%s]: rsi=%.1f macd_hist=%.4f px=%.4f sma5=%.4f bbL=%.4f",
                    symbol,
                    rsi,
                    macd_hist,
                    current_price,
                    sma5,
                    bb_lower,
                )
                # Override into standard BUY flow (still respects ROR gate + bucket caps)
                buy_reason = "tech_entry"
                confidence = max(confidence, 0.35)
                prediction = threshold_up * 1.0002

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
                    mc["win_rate"],
                    mc["var95"],
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
                    latest = featured.iloc[-1]
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
                self._execute(symbol, "BUY", buy_qty, current_price, buy_reason)
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
        # In NO_FUTU mode, try to load positions from state.json as fallback
        import os as _os
        state_file = _os.path.join(_os.path.dirname(__file__), "..", "..", "state.json")

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

    def _execute(self, symbol: str, side: str, qty: int, price: float, reason: str) -> None:
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

        if side == "BUY":
            self.position_qty_by_symbol[symbol] += qty
            self.highest_price_since_entry_by_symbol[symbol] = fill_price
            self.entry_price_by_symbol[symbol] = fill_price
            self.cycles_since_buy_by_symbol[symbol] = 0
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

        # Persist NO_FUTU paper positions into state.json to avoid repeated SELL/BUY after restart
        if _os.getenv("NO_FUTU") == "1":
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
