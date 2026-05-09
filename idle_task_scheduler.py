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

import pandas as pd

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
    universe_mode: str = "core"
    max_symbols_per_session: int = 0
    history_period: str = "60d"
    history_interval: str = "1d"
    skip_if_latest_within_days: int = 0
    universe_files: list[str] = field(default_factory=lambda: ["data/universe/us_symbols.txt", "data/universe/hk_symbols.txt"])
    cursor_path: str = "state/idle_backfill_cursor.json"

    @classmethod
    def from_dict(cls, d: dict) -> "IdleSchedulerConfig":
        universe_files = d.get("universe_files")
        if universe_files is None:
            universe_files = ["data/universe/us_symbols.txt", "data/universe/hk_symbols.txt"]
        elif isinstance(universe_files, str):
            universe_files = [universe_files]
        return cls(
            max_concurrent_symbols=int(d.get("max_concurrent_symbols", 5)),
            max_klines_per_batch=int(d.get("max_klines_per_batch", 100)),
            cooldown_between_batches_sec=float(d.get("cooldown_between_batches_sec", 2.0)),
            max_idle_hours_per_day=float(d.get("max_idle_hours_per_day", 6.0)),
            stop_before_open_min=int(d.get("stop_before_open_min", 30)),
            enabled=bool(d.get("enabled", True)),
            universe_mode=str(d.get("universe_mode", "core")).lower(),
            max_symbols_per_session=int(d.get("max_symbols_per_session", 0)),
            history_period=str(d.get("history_period", "60d")),
            history_interval=str(d.get("history_interval", "1d")),
            skip_if_latest_within_days=int(d.get("skip_if_latest_within_days", 0)),
            universe_files=[str(p) for p in universe_files],
            cursor_path=str(d.get("cursor_path", "state/idle_backfill_cursor.json")),
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


def _dedupe_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in symbols:
        sym = str(raw).strip()
        if not sym or sym.startswith("#"):
            continue
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def _read_symbol_file(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        if p.suffix.lower() == ".json":
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("symbols", data.get("picks", []))
            if isinstance(data, list):
                return _dedupe_symbols([str(x) for x in data])
            return []
        symbols: list[str] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            symbols.extend(part.strip() for part in line.split(","))
        return _dedupe_symbols(symbols)
    except Exception:
        return []


def _load_universe_symbols(cfg: IdleSchedulerConfig) -> list[str]:
    symbols: list[str] = []
    for path in cfg.universe_files:
        symbols.extend(_read_symbol_file(path))
    return _dedupe_symbols(symbols)


def _read_cursor(path: str) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return max(0, int(data.get("offset", 0)))
    except Exception:
        return 0


def _write_cursor(path: str, offset: int, total: int) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "offset": offset % max(total, 1),
        "total": total,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _rotate_symbols(symbols: list[str], cfg: IdleSchedulerConfig, logger: logging.Logger) -> list[str]:
    if not symbols:
        return []
    limit = cfg.max_symbols_per_session
    if limit <= 0 or limit >= len(symbols):
        return symbols
    offset = _read_cursor(cfg.cursor_path) % len(symbols)
    rotated = symbols[offset:] + symbols[:offset]
    selected = rotated[:limit]
    _write_cursor(cfg.cursor_path, offset + len(selected), len(symbols))
    logger.info("[universe] rotation selected %d/%d symbols from offset=%d", len(selected), len(symbols), offset)
    return selected


def _resolve_backfill_symbols(
    fallback_symbols: list[str],
    cfg: IdleSchedulerConfig,
    logger: logging.Logger,
) -> list[str]:
    fallback = _dedupe_symbols(fallback_symbols)
    mode = cfg.universe_mode.lower()
    if mode == "core":
        return fallback[: cfg.max_symbols_per_session] if cfg.max_symbols_per_session > 0 else fallback

    universe = _load_universe_symbols(cfg)
    if not universe:
        logger.warning("[universe] mode=%s but no universe files loaded; falling back to core symbols", mode)
        return fallback[: cfg.max_symbols_per_session] if cfg.max_symbols_per_session > 0 else fallback

    if mode == "rotation":
        return _rotate_symbols(universe, cfg, logger)
    if mode == "full":
        return universe

    logger.warning("[universe] unknown mode=%s; falling back to core symbols", mode)
    return fallback[: cfg.max_symbols_per_session] if cfg.max_symbols_per_session > 0 else fallback


# ── Task implementations ──────────────────────────────────────────────────────

def _normalize_history_for_db(hist) -> "pd.DataFrame":
    """Return OHLCV history with Date/Open/High/Low/Close/Volume columns."""
    if hist is None or hist.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    frame = hist.copy()
    if "Date" not in frame.columns:
        frame = frame.reset_index()
    if "Datetime" in frame.columns and "Date" not in frame.columns:
        frame = frame.rename(columns={"Datetime": "Date"})
    if "index" in frame.columns and "Date" not in frame.columns:
        frame = frame.rename(columns={"index": "Date"})

    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if any(col not in frame.columns for col in required):
        return pd.DataFrame(columns=required)

    return frame[required].dropna(subset=["Date", "Close"])


def _sync_history_to_market_db(
    symbol: str,
    hist,
    loop: "LiveTradingLoop",
    logger: logging.Logger,
) -> bool:
    """Persist idle backfill history to market_data with indicator features."""
    db = _get_market_db(loop)
    if db is None:
        return False

    frame = _normalize_history_for_db(hist)
    if frame.empty:
        return False

    try:
        generator = getattr(loop, "feature_generator", None)
        if generator is not None:
            frame = generator.generate(frame)
        db.save_data(frame, symbol=symbol)
        return True
    except Exception as exc:
        logger.warning("[backfill][%s] DB sync failed: %s", symbol, exc)
        return False


def _get_market_db(loop: "LiveTradingLoop"):
    get_db = getattr(loop, "_get_market_db", None)
    if callable(get_db):
        return get_db()
    return getattr(loop, "market_db", None)


def _latest_is_fresh(symbol: str, loop: "LiveTradingLoop", cfg: IdleSchedulerConfig) -> bool:
    if cfg.skip_if_latest_within_days <= 0:
        return False
    db = _get_market_db(loop)
    if db is None or not hasattr(db, "get_latest_data"):
        return False
    try:
        latest = db.get_latest_data(symbol, limit=1)
        if latest is None or latest.empty or "timestamp" not in latest.columns:
            return False
        ts = pd.to_datetime(latest.iloc[0]["timestamp"], errors="coerce", utc=True)
        if pd.isna(ts):
            return False
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=cfg.skip_if_latest_within_days)
        return ts >= cutoff
    except Exception:
        return False


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
        raw_batch = symbols[i : i + cfg.max_concurrent_symbols]
        batch = [sym for sym in raw_batch if not _latest_is_fresh(sym, loop, cfg)]
        if not batch:
            _emit(
                logger,
                "backfill",
                symbols_processed=0,
                skipped_fresh=len(raw_batch),
                duration_ms=0,
                quota_remaining_h=round(rate_limiter.remaining_hours(), 2),
            )
            continue
        t0 = time.time()
        try:
            history_map = await asyncio.to_thread(
                download_history, batch, cfg.history_period, cfg.history_interval
            )
            db_synced = 0
            for sym, hist in history_map.items():
                if not hist.empty:
                    hist = hist.reset_index()
                    if sym in loop.market_buffers and loop.market_buffers[sym].empty:
                        loop.market_buffers[sym] = hist
                    if _sync_history_to_market_db(sym, hist, loop, logger):
                        db_synced += 1
                    processed += 1
                else:
                    logger.warning("[backfill][%s] empty history from yf_provider", sym)
            duration_ms = int((time.time() - t0) * 1000)
            _emit(
                logger,
                "backfill",
                symbols_processed=len(batch),
                skipped_fresh=len(raw_batch) - len(batch),
                db_synced=db_synced,
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
        fallback_symbols = self.loop.symbols if self.loop.symbols else self._idle_symbols
        symbols = _resolve_backfill_symbols(fallback_symbols, self.cfg, self.logger)
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

            # Task 2: Indicator warmup
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
