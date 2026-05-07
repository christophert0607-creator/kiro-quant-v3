"""
IdleTaskScheduler — off-hours data preloading for Kiro Quant V3.

Runs during each market's off-hours with a configurable budget:
  1. Historical backfill (daily / 1-min K-lines for all configured symbols)
  2. Indicator warm-up (pre-compute TA on all timeframes)
  3. Pre-market readiness report (cache coverage, quote source, last sync)

Rate limits come from config.json["idle_scheduler"]; never uses hardcoded quotas.
Gracefully stops 30 minutes before the next market open.
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
        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)
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


# ── Task implementations ──────────────────────────────────────────────────────

async def _run_historical_backfill(
    symbols: list[str],
    loop: "LiveTradingLoop",
    cfg: IdleSchedulerConfig,
    rate_limiter: IdleRateLimiter,
    logger: logging.Logger,
) -> int:
    """Fetch daily K-lines for all symbols in config-sized batches."""
    processed = 0
    for i in range(0, len(symbols), cfg.max_concurrent_symbols):
        if not rate_limiter.check_budget():
            logger.warning("[backfill] Budget exhausted — stopping early")
            break
        batch = symbols[i : i + cfg.max_concurrent_symbols]
        t0 = time.time()
        try:
            for sym in batch:
                try:
                    import yfinance as yf
                    hist = await asyncio.to_thread(
                        lambda s=sym: yf.Ticker(s).history(period="60d", interval="1d")
                    )
                    if not hist.empty:
                        hist.index.name = "Date"
                        hist = hist.reset_index()
                        if sym in loop.market_buffers and loop.market_buffers[sym].empty:
                            loop.market_buffers[sym] = hist
                        processed += 1
                except Exception as exc:
                    logger.warning("[backfill][%s] failed: %s", sym, exc)
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


# ── Scheduler ─────────────────────────────────────────────────────────────────

class IdleTaskScheduler:
    """
    Off-hours task scheduler. Call `run()` once per IDLE session.

    Usage in v3_launcher.py:
        scheduler = IdleTaskScheduler(loop, cfg=IdleSchedulerConfig.from_config_json())
        asyncio.create_task(scheduler.run())
    """

    def __init__(
        self,
        loop: "LiveTradingLoop",
        cfg: IdleSchedulerConfig | None = None,
        config_path: str = "config.json",
    ) -> None:
        self.loop = loop
        self.cfg = cfg or IdleSchedulerConfig.from_config_json(config_path)
        self.logger = _build_idle_logger()
        self._stop_event = asyncio.Event()

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
        hk_open_today = hk_now.replace(
            hour=9, minute=30, second=0, microsecond=0
        )
        # Next US open (HKT): 21:30
        us_open_today = hk_now.replace(
            hour=21, minute=30, second=0, microsecond=0
        )

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

        symbols = self.loop.symbols
        if not symbols:
            self.logger.info("[IdleTaskScheduler] no symbols configured, skipping")
            return

        rate_limiter = IdleRateLimiter(self.cfg)
        _emit(self.logger, "start", symbols=symbols, cfg=self.cfg.__dict__)
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
                self.logger.info("[IdleTaskScheduler] Too close to market open (%.0f min) — skipping", mins_left)
                return

            t0 = time.time()
            n = await _run_historical_backfill(symbols, self.loop, self.cfg, rate_limiter, self.logger)
            self.logger.info("[backfill] done: %d symbols in %.1fs", n, time.time() - t0)

            # Task 2: Indicator warmup
            if self._stop_event.is_set():
                return
            mins_left = self._minutes_until_next_open()
            if mins_left <= self.cfg.stop_before_open_min:
                self.logger.info("[IdleTaskScheduler] Stopping before open (%.0f min left)", mins_left)
                return

            t0 = time.time()
            n = await _run_indicator_warmup(symbols, self.loop, self.cfg, rate_limiter, self.logger)
            self.logger.info("[warmup] done: %d symbols in %.1fs", n, time.time() - t0)

            # Task 3: Readiness report
            if self._stop_event.is_set():
                return
            _build_readiness_report(symbols, self.loop, self.logger)

        except asyncio.CancelledError:
            self.logger.info("[IdleTaskScheduler] cancelled")
            raise
        except Exception as exc:
            self.logger.error("[IdleTaskScheduler] unexpected error: %s", exc)
        finally:
            _emit(self.logger, "complete", elapsed_h=round((time.time() - rate_limiter._budget_start) / 3600, 3))
            self.logger.info("[IdleTaskScheduler] done")
