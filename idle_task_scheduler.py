"""
IdleTaskScheduler — off-hours data preloading for Kiro Quant V3.

Runs during each market's off-hours with a configurable budget:
  1. Historical backfill (daily / 1-min K-lines for all configured symbols)
  2. Indicator warm-up (pre-compute TA on all timeframes)
  3. Pre-market readiness report (cache coverage, quote source, last sync)

Rate limits come from config.json["idle_scheduler"]; never uses hardcoded quotas.
Gracefully stops 30 minutes before the next market open.

Singleton protection: at most one scheduler task runs per IDLE session.
All yfinance access is routed through v3_pipeline.data.yf_provider.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from v3_pipeline.core.main_loop import LiveTradingLoop


# ── Logging ───────────────────────────────────────────────────────────────────

def _get_idle_log_dir() -> Path:
    """Return the log directory, honouring KIRO_LOG_DIR env var for test isolation."""
    override = os.environ.get("KIRO_LOG_DIR")
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    default = Path(__file__).parent / "logs"
    default.mkdir(exist_ok=True)
    return default


def _build_idle_logger() -> logging.Logger:
    logger = logging.getLogger("kiro.idle_scheduler")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    logger.addHandler(console)
    try:
        from logging.handlers import RotatingFileHandler
        log_dir = _get_idle_log_dir()
        fh = RotatingFileHandler(
            log_dir / "idle_scheduler.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except Exception as exc:
        sys.stderr.write(f"[idle_scheduler] log file setup failed: {exc}\n")
    logger.propagate = False
    return logger


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class IdleSchedulerConfig:
    max_concurrent_symbols: int = 5
    max_klines_per_batch: int = 100
    cooldown_between_batches_sec: float = 2.0
    max_idle_hours_per_day: float = 6.0
    stop_before_open_min: int = 30
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "IdleSchedulerConfig":
        return cls(
            max_concurrent_symbols=int(d.get("max_concurrent_symbols", 5)),
            max_klines_per_batch=int(d.get("max_klines_per_batch", 100)),
            cooldown_between_batches_sec=float(d.get("cooldown_between_batches_sec", 2.0)),
            max_idle_hours_per_day=float(d.get("max_idle_hours_per_day", 6.0)),
            stop_before_open_min=int(d.get("stop_before_open_min", 30)),
            enabled=bool(d.get("enabled", True)),
        )

    @classmethod
    def from_config_json(cls, config_path: str = "config.json") -> "IdleSchedulerConfig":
        try:
            p = Path(config_path)
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                return cls.from_dict(data.get("idle_scheduler", {}))
        except Exception:
            pass
        return cls()


# ── Rate Limiter ──────────────────────────────────────────────────────────────

class IdleRateLimiter:
    """Tracks consumed quota against the configured daily budget."""

    def __init__(self, cfg: IdleSchedulerConfig) -> None:
        self._cfg = cfg
        self._budget_start: float = time.time()
        self._elapsed_hours: float = 0.0

    def check_budget(self) -> bool:
        """Return True if idle budget is still available."""
        self._elapsed_hours = (time.time() - self._budget_start) / 3600.0
        return self._elapsed_hours < self._cfg.max_idle_hours_per_day

    def remaining_hours(self) -> float:
        return max(0.0, self._cfg.max_idle_hours_per_day - self._elapsed_hours)


# ── Structured emit ───────────────────────────────────────────────────────────

def _emit(logger: logging.Logger, event_type: str, **fields) -> None:
    try:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": "idle_task",
            "task_type": event_type,
            **fields,
        }
        logger.info(json.dumps(record, ensure_ascii=False))
    except Exception:
        pass


def _emit_fd_health(logger: logging.Logger, mode: str = "check") -> None:
    """Emit a structured fd_health event."""
    try:
        from v3_pipeline.data.yf_provider import fd_health_event
        health = fd_health_event(mode)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **health,
        }
        logger.info(json.dumps(record, ensure_ascii=False))
    except Exception:
        pass


# ── Task implementations ──────────────────────────────────────────────────────

def _run_kline_refresh(symbols: list[str], logger: logging.Logger) -> tuple[int, int]:
    """
    Synchronous wrapper for KlineBackfill — intended to be called via asyncio.to_thread.
    Returns (rows_repaired, rows_updated).
    """
    try:
        from v3_pipeline.data.kline_backfill import KlineBackfill
        kb = KlineBackfill()
        repair = kb.run_smart_repair(symbols)
        incremental = kb.run_incremental(symbols)
        rows_repaired = sum(v for v in repair.values() if v > 0)
        rows_updated = sum(v for v in incremental.values() if v > 0)
        return rows_repaired, rows_updated
    except Exception as exc:
        logger.error("[db_refresh] KlineBackfill failed: %s", exc)
        return 0, 0

async def _run_historical_backfill(
    symbols: list[str],
    loop: "LiveTradingLoop",
    cfg: IdleSchedulerConfig,
    rate_limiter: IdleRateLimiter,
    logger: logging.Logger,
) -> int:
    """Fetch daily K-lines for all symbols using the centralized yf_provider."""
    from v3_pipeline.data.yf_provider import download_history

    processed = 0
    for i in range(0, len(symbols), cfg.max_concurrent_symbols):
        if not rate_limiter.check_budget():
            logger.warning("[backfill] Budget exhausted — stopping early")
            break
        batch = symbols[i : i + cfg.max_concurrent_symbols]
        t0 = time.time()
        try:
            history_map = await asyncio.to_thread(
                download_history, batch, "60d", "1d"
            )
            for sym, hist in history_map.items():
                if not hist.empty:
                    hist = hist.reset_index()
                    if sym in loop.market_buffers and loop.market_buffers[sym].empty:
                        loop.market_buffers[sym] = hist
                    processed += 1
                else:
                    logger.warning("[backfill][%s] empty history from yf_provider", sym)
            duration_ms = int((time.time() - t0) * 1000)
            _emit(
                logger,
                "backfill",
                symbols_processed=len(batch),
                duration_ms=duration_ms,
                quota_remaining_h=round(rate_limiter.remaining_hours(), 2),
            )
        except Exception as exc:
            logger.warning("[backfill] batch error: %s", exc)
        await asyncio.sleep(cfg.cooldown_between_batches_sec)
    return processed


def _run_mds_filter(
    symbols: list[str],
    loop: "LiveTradingLoop",
    logger: logging.Logger,
) -> list[str]:
    """Compute MDS scores and return filtered symbol list (§9.5).

    Only runs when pool > 50 symbols. Keeps the top 20% most consistent
    intraday patterns (lowest Fréchet variation score), floored at 10 symbols.
    Returns original list unchanged when pool is small or data is missing.
    """
    if len(symbols) <= 50:
        return symbols

    try:
        from v3_pipeline.data.mds_filter import score_symbols, filter_universe, save_scores
        buffers = {s: loop.market_buffers[s] for s in symbols if s in loop.market_buffers}
        if not buffers:
            return symbols

        scores = score_symbols(buffers)
        filtered = filter_universe(scores)

        # Preserve symbols with no buffer data (no kline yet) in full run
        no_data = [s for s in symbols if s not in buffers]
        result = filtered + no_data

        save_scores(scores)
        _emit(
            logger,
            "mds_filter",
            input_symbols=len(symbols),
            scored=len(scores),
            filtered=len(filtered),
            no_data=len(no_data),
            output_symbols=len(result),
        )
        logger.info(
            "[mds_filter] %d → %d symbols (scored=%d, no_data=%d)",
            len(symbols), len(result), len(filtered), len(no_data),
        )
        return result
    except Exception as exc:
        logger.warning("[mds_filter] failed, using full symbol list: %s", exc)
        return symbols


async def _run_indicator_warmup(
    symbols: list[str],
    loop: "LiveTradingLoop",
    cfg: IdleSchedulerConfig,
    rate_limiter: IdleRateLimiter,
    logger: logging.Logger,
) -> int:
    """Pre-compute technical indicators on all timeframes for each symbol."""
    processed = 0
    for i in range(0, len(symbols), cfg.max_concurrent_symbols):
        if not rate_limiter.check_budget():
            logger.warning("[warmup] Budget exhausted — stopping early")
            break
        batch = symbols[i : i + cfg.max_concurrent_symbols]
        t0 = time.time()
        warmed = 0
        for sym in batch:
            df = loop.market_buffers.get(sym)
            if df is None or len(df) < 20:
                continue
            try:
                loop.feature_generator.generate(df)
                warmed += 1
            except Exception as exc:
                logger.warning("[warmup][%s] indicator gen failed: %s", sym, exc)
        duration_ms = int((time.time() - t0) * 1000)
        processed += warmed
        _emit(
            logger,
            "indicator_warmup",
            symbols_processed=warmed,
            duration_ms=duration_ms,
            quota_remaining_h=round(rate_limiter.remaining_hours(), 2),
        )
        await asyncio.sleep(cfg.cooldown_between_batches_sec)
    return processed


def _build_readiness_report(
    symbols: list[str],
    loop: "LiveTradingLoop",
    logger: logging.Logger,
) -> dict:
    """Build a per-symbol readiness summary and log it."""
    report: dict[str, dict] = {}
    for sym in symbols:
        df = loop.market_buffers.get(sym)
        if df is None or df.empty:
            report[sym] = {"status": "no_data", "bars": 0}
            continue
        bars = len(df)
        has_close = "Close" in df.columns
        last_bar_ts = str(df.index[-1]) if has_close else "unknown"
        report[sym] = {
            "status": "ok" if bars >= 60 else "insufficient",
            "bars": bars,
            "last_bar": last_bar_ts,
        }
    ok_count = sum(1 for v in report.values() if v.get("status") == "ok")
    _emit(
        logger,
        "readiness_report",
        total_symbols=len(symbols),
        ready=ok_count,
        not_ready=len(symbols) - ok_count,
        per_symbol=report,
    )
    logger.info(
        "[readiness] %d/%d symbols ready (≥60 bars)", ok_count, len(symbols)
    )
    return report


# ── Singleton guard ───────────────────────────────────────────────────────────

# Tracks running IdleTaskScheduler tasks by a session key to prevent double-start.
_active_idle_sessions: set[str] = set()
_active_idle_lock = asyncio.Lock() if False else None  # created lazily per event loop


def _get_active_lock() -> asyncio.Lock:
    global _active_idle_lock
    if _active_idle_lock is None:
        _active_idle_lock = asyncio.Lock()
    return _active_idle_lock


# ── Scheduler ─────────────────────────────────────────────────────────────────

class IdleTaskScheduler:
    """
    Off-hours task scheduler. Call `run()` once per IDLE session.

    Singleton protection: if `run()` is already in progress for the same
    session key, subsequent calls return immediately without starting a
    second batch of tasks.

    Usage in v3_launcher.py:
        scheduler = IdleTaskScheduler(
            loop,
            cfg=IdleSchedulerConfig.from_config_json(),
            idle_symbols=IDLE_COLLECTION_SYMBOLS,
        )
        asyncio.create_task(scheduler.run())
    """

    def __init__(
        self,
        loop: "LiveTradingLoop",
        cfg: IdleSchedulerConfig | None = None,
        config_path: str = "config.json",
        idle_symbols: list[str] | None = None,
        session_key: str | None = None,
    ) -> None:
        self.loop = loop
        self.cfg = cfg or IdleSchedulerConfig.from_config_json(config_path)
        self.logger = _build_idle_logger()
        self._stop_event = asyncio.Event()
        # Fallback symbol universe when loop.symbols is empty in IDLE mode
        self._idle_symbols = idle_symbols or []
        # Session key for singleton protection (default: daily UTC date)
        self._session_key = session_key or datetime.now(timezone.utc).strftime("idle_%Y%m%d")

    def request_stop(self) -> None:
        """Signal the scheduler to stop gracefully."""
        self._stop_event.set()

    def _minutes_until_next_open(self) -> float:
        """Return minutes until the next HK or US market open (HKT clock)."""
        from zoneinfo import ZoneInfo
        from datetime import time as dt_time

        HK_TZ = ZoneInfo("Asia/Hong_Kong")
        now = datetime.now(HK_TZ)
        weekday = now.weekday()
        hk_now = now.replace(tzinfo=None)

        # Next HK open: 09:30
        hk_open_today = hk_now.replace(hour=9, minute=30, second=0, microsecond=0)
        # Next US open (HKT): 21:30
        us_open_today = hk_now.replace(hour=21, minute=30, second=0, microsecond=0)

        candidates: list[float] = []
        if hk_open_today > hk_now and weekday < 5:
            candidates.append((hk_open_today - hk_now).total_seconds() / 60)
        if us_open_today > hk_now and weekday < 5:
            candidates.append((us_open_today - hk_now).total_seconds() / 60)

        # If no candidate today, next HK open is tomorrow
        if not candidates:
            tomorrow = hk_now + timedelta(days=1)
            nxt = tomorrow.replace(hour=9, minute=30, second=0, microsecond=0)
            candidates.append((nxt - hk_now).total_seconds() / 60)

        return min(candidates)

    async def run(self) -> None:
        if not self.cfg.enabled:
            self.logger.info("[IdleTaskScheduler] disabled via config, skipping")
            return

        # ── Singleton protection ───────────────────────────────────────────
        lock = _get_active_lock()
        async with lock:
            if self._session_key in _active_idle_sessions:
                self.logger.info(
                    "[IdleTaskScheduler] session '%s' already running — skipping duplicate start",
                    self._session_key,
                )
                return
            _active_idle_sessions.add(self._session_key)

        try:
            await self._run_tasks()
        except Exception:
            # On failure, release the key so a manual restart is possible.
            async with lock:
                _active_idle_sessions.discard(self._session_key)
            raise
        # On success, keep the key in the set — the session is "used up"
        # for this key and must not re-run (prevents double-fire if the
        # IDLE→IDLE transition repeats within the same day).

    async def _run_tasks(self) -> None:
        # Use loop.symbols if non-empty; otherwise fall back to idle_symbols
        symbols = self.loop.symbols if self.loop.symbols else self._idle_symbols
        if not symbols:
            self.logger.info("[IdleTaskScheduler] no symbols available, skipping")
            return

        rate_limiter = IdleRateLimiter(self.cfg)
        _emit(self.logger, "start", symbols=symbols[:10], total=len(symbols), cfg=self.cfg.__dict__)
        _emit_fd_health(self.logger, "session_start")
        self.logger.info(
            "[IdleTaskScheduler] Starting — %d symbols, budget=%.1fh, stop_before_open=%dmin",
            len(symbols), self.cfg.max_idle_hours_per_day, self.cfg.stop_before_open_min,
        )

        try:
            # Task 1: Historical backfill
            if self._stop_event.is_set():
                return
            mins_left = self._minutes_until_next_open()
            if mins_left <= self.cfg.stop_before_open_min:
                self.logger.info(
                    "[IdleTaskScheduler] Too close to market open (%.0f min) — skipping", mins_left
                )
                return

            t0 = time.time()
            n = await _run_historical_backfill(
                symbols, self.loop, self.cfg, rate_limiter, self.logger
            )
            self.logger.info("[backfill] done: %d symbols in %.1fs", n, time.time() - t0)
            _emit_fd_health(self.logger, "post_backfill")

            # Task 1.5: MDS pre-filter — only when pool > 50 symbols (§9.5)
            if len(symbols) > 50:
                t0 = time.time()
                symbols = await asyncio.to_thread(
                    _run_mds_filter, symbols, self.loop, self.logger
                )
                self.logger.info("[mds_filter] done in %.1fs → %d symbols", time.time() - t0, len(symbols))

            # Task 2: Indicator warmup (on MDS-filtered symbol set)
            if self._stop_event.is_set():
                return
            mins_left = self._minutes_until_next_open()
            if mins_left <= self.cfg.stop_before_open_min:
                self.logger.info(
                    "[IdleTaskScheduler] Stopping before open (%.0f min left)", mins_left
                )
                return

            t0 = time.time()
            n = await _run_indicator_warmup(
                symbols, self.loop, self.cfg, rate_limiter, self.logger
            )
            self.logger.info("[warmup] done: %d symbols in %.1fs", n, time.time() - t0)

            # Task 3: Readiness report
            if self._stop_event.is_set():
                return
            _build_readiness_report(symbols, self.loop, self.logger)

            # Task 4: DB K-line backfill + incremental update (kiro_quant.db)
            if self._stop_event.is_set():
                return
            mins_left = self._minutes_until_next_open()
            if mins_left <= self.cfg.stop_before_open_min:
                self.logger.info(
                    "[IdleTaskScheduler] Too close to open (%.0f min) — skipping DB refresh", mins_left
                )
            else:
                t0 = time.time()
                rows_repaired, rows_updated = await asyncio.to_thread(
                    _run_kline_refresh, symbols, self.logger
                )
                self.logger.info(
                    "[db_refresh] done in %.1fs — repaired=%d updated=%d",
                    time.time() - t0, rows_repaired, rows_updated,
                )
                _emit(
                    self.logger,
                    "db_kline_refresh",
                    rows_repaired=rows_repaired,
                    rows_updated=rows_updated,
                    duration_sec=round(time.time() - t0, 1),
                )

            # Task 5: DB archive — move old live rows to archive table (PR #83)
            if self._stop_event.is_set():
                return
            try:
                if hasattr(self.loop, 'db_persist') and self.loop.db_persist is not None:
                    t0 = time.time()
                    archived = await self.loop.db_persist.archive_old_data()
                    self.logger.info(
                        '[db_archive] done in %.1fs — archived=%d rows',
                        time.time() - t0,
                        archived,
                    )
                    _emit(
                        self.logger,
                        'db_archive',
                        archived_rows=archived,
                        duration_sec=round(time.time() - t0, 1),
                    )
            except Exception as exc:
                self.logger.warning('[db_archive] failed: %s', exc)

        except asyncio.CancelledError:
            self.logger.info("[IdleTaskScheduler] cancelled")
            raise
        except Exception as exc:
            self.logger.error("[IdleTaskScheduler] unexpected error: %s", exc)
        finally:
            _emit_fd_health(self.logger, "session_end")
            _emit(
                self.logger,
                "complete",
                elapsed_h=round((time.time() - rate_limiter._budget_start) / 3600, 3),
            )
            self.logger.info("[IdleTaskScheduler] done")
