"""
KlineBackfill — historical K-line data backfill and maintenance for kiro_quant.db.

Responsibilities:
  - Initial full backfill for symbols missing ≥MIN_BARS daily bars
  - Incremental daily updates (last 5d) for all watchlist symbols
  - Proper data_source tagging: "YF_HIST" (backfill) / "YF_LIVE" (incremental)
  - Timezone-normalised timestamps (UTC naive, no offset suffix)
  - Batch parallel processing with resume capability for 300+ symbols

Integration point: called from IdleTaskScheduler Task 4 via asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("kiro.kline_backfill")

_REPO_ROOT = Path(__file__).parent.parent.parent
_DB_PATH = _REPO_ROOT / "kiro_quant.db"
_PROGRESS_PATH = _REPO_ROOT / "data" / "backfill_progress.json"

MIN_BARS = 60
FULL_PERIOD = "2y"
INCREMENTAL_PERIOD = "5d"
_DEFAULT_BATCH_SIZE = 10
_DEFAULT_COOLDOWN_SEC = 2.0
_DEFAULT_MAX_WORKERS = int(os.getenv("BACKFILL_MAX_WORKERS", "4"))
_DEFAULT_YF_CONCURRENT = int(os.getenv("YF_MAX_CONCURRENT", "2"))


# ── Timestamp normalisation ───────────────────────────────────────────────────

def _to_utc_naive_str(ts: object) -> str:
    """Convert any timestamp (pd.Timestamp, datetime, str) to a UTC-naive ISO string."""
    if isinstance(ts, (pd.Timestamp, datetime)):
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)  # type: ignore[union-attr]
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    # Raw string from DB (e.g. "2026-03-17 15:59:00-04:00") — strip offset
    raw = str(ts).split(".")[0]  # drop microseconds
    for sep in ("+", "-0", "-1"):             # crude offset strip
        if sep in raw[10:]:                   # only look past the date portion
            raw = raw[: raw.rfind(sep, 10)]
    return raw.strip()


# ── DataFrame preparation ─────────────────────────────────────────────────────

def _prepare_ohlcv(hist: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise a raw ticker.history() DataFrame for TechnicalIndicatorGenerator.

    Output has the exact columns generate() needs:
      Date, Open, High, Low, Close, Volume  (capital letters, Date is string UTC-naive)
    """
    if hist.empty:
        return pd.DataFrame()

    df = hist.reset_index()

    # ticker.history(interval="1d") → index named "Date"; intraday → "Datetime"
    for col in ("Datetime", "Date"):
        if col in df.columns:
            df = df.rename(columns={col: "Date"})
            break

    # Keep only what generate() and the DB need; drop Dividends / Stock Splits
    keep = ["Date", "Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in keep if c in df.columns]].copy()

    if "Date" not in df.columns:
        logger.warning("[kline_backfill] no Date column after reset_index, skipping frame")
        return pd.DataFrame()

    df["Date"] = df["Date"].apply(_to_utc_naive_str)
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df


def _to_db_frame(featured: pd.DataFrame, symbol: str, data_source: str) -> pd.DataFrame:
    """Rename Date→timestamp, add symbol/data_source, ready for DatabaseManager.save_data()."""
    df = featured.rename(columns={
        "Date": "timestamp",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }).copy()
    df["symbol"] = symbol
    df["data_source"] = data_source
    return df


# ── Progress tracking ─────────────────────────────────────────────────────────

class BackfillProgress:
    """Track backfill progress to enable resume after interruption."""

    def __init__(self, path: Path = _PROGRESS_PATH):
        self.path = path
        self._completed: set[str] = set()
        self._failed: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._completed = set(data.get("completed", []))
            self._failed = data.get("failed", {})
        except Exception as exc:
            logger.warning("[kline_backfill] Failed to load progress: %s", exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({
                    "completed": sorted(self._completed),
                    "failed": self._failed,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }, f, indent=2)
        except Exception as exc:
            logger.warning("[kline_backfill] Failed to save progress: %s", exc)

    def is_done(self, symbol: str) -> bool:
        return symbol in self._completed

    def mark_done(self, symbol: str) -> None:
        self._completed.add(symbol)
        self._failed.pop(symbol, None)

    def mark_failed(self, symbol: str, reason: str) -> None:
        self._failed[symbol] = reason

    def reset(self, symbols: Optional[list[str]] = None) -> None:
        if symbols is None:
            self._completed.clear()
            self._failed.clear()
        else:
            for s in symbols:
                self._completed.discard(s)
                self._failed.pop(s, None)
        self.save()

    @property
    def stats(self) -> dict:
        return {
            "completed": len(self._completed),
            "failed": len(self._failed),
        }


# ── Main class ────────────────────────────────────────────────────────────────

class KlineBackfill:
    """
    Fetch, compute TA, and persist daily K-line data to kiro_quant.db.

    Typical usage inside IdleTaskScheduler (Task 4):
        kb = KlineBackfill()
        repair_counts  = kb.run_smart_repair(IDLE_COLLECTION_SYMBOLS)
        update_counts  = kb.run_incremental(IDLE_COLLECTION_SYMBOLS)
    """

    def __init__(
        self,
        db_path: str | None = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        cooldown_sec: float = _DEFAULT_COOLDOWN_SEC,
        max_workers: int = _DEFAULT_MAX_WORKERS,
    ) -> None:
        # Ensure repo root is importable regardless of cwd
        repo = str(_REPO_ROOT)
        if repo not in sys.path:
            sys.path.insert(0, repo)

        from db_manager import DatabaseManager
        from v3_pipeline.features.indicators import TechnicalIndicatorGenerator

        self._db = DatabaseManager(db_path or str(_DB_PATH))
        self._indicator_gen = TechnicalIndicatorGenerator()
        self._batch_size = batch_size
        self._cooldown_sec = cooldown_sec
        self._max_workers = max_workers
        self._progress = BackfillProgress()

    # ── Health check ──────────────────────────────────────────────────────────

    def symbol_health(self, symbols: list[str]) -> dict[str, dict]:
        return self._db.check_health(symbols)

    def symbols_needing_backfill(self, symbols: list[str]) -> list[str]:
        health = self.symbol_health(symbols)
        return [s for s in symbols if (health.get(s) or {}).get("count", 0) < MIN_BARS]

    # ── Core fetch + save (single symbol) ─────────────────────────────────────

    def _fetch_one(
        self,
        symbol: str,
        period: str,
        data_source: str,
    ) -> tuple[str, int, Optional[str]]:
        """
        Download, compute TA, and save a single symbol.
        Returns (symbol, rows_saved, error_or_none).
        """
        from v3_pipeline.data.yf_provider import download_history

        try:
            hist_map = download_history([symbol], period=period, interval="1d")
            hist = hist_map.get(symbol, pd.DataFrame())
            if hist.empty:
                return symbol, 0, "empty history"

            ohlcv = _prepare_ohlcv(hist)
            if ohlcv.empty:
                return symbol, 0, "empty after prepare"

            featured = self._indicator_gen.generate(ohlcv)
            db_frame = _to_db_frame(featured, symbol, data_source)

            self._db.save_data(db_frame, symbol=symbol)
            return symbol, len(db_frame), None
        except Exception as exc:
            return symbol, 0, str(exc)

    # ── Parallel batch processing ─────────────────────────────────────────────

    def _fetch_and_save_parallel(
        self,
        symbols: list[str],
        period: str,
        data_source: str,
        skip_completed: bool = True,
    ) -> dict[str, int]:
        """
        Parallel fetch + save with ThreadPoolExecutor.
        Respects max_workers and yfinance concurrency limits.
        Returns {symbol: rows_saved}.
        """
        saved: dict[str, int] = {}
        to_process = [
            s for s in symbols
            if not (skip_completed and self._progress.is_done(s))
        ]

        if not to_process:
            logger.info("[kline_backfill] all %d symbols already completed", len(symbols))
            return saved

        logger.info(
            "[kline_backfill] parallel %s: %d symbols (workers=%d, batch=%d)",
            data_source, len(to_process), self._max_workers, self._batch_size,
        )

        total = len(to_process)
        completed = 0
        t0_all = time.time()

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            # Submit all futures
            future_to_sym = {
                executor.submit(self._fetch_one, sym, period, data_source): sym
                for sym in to_process
            }

            for future in as_completed(future_to_sym):
                sym = future_to_sym[future]
                try:
                    symbol, rows, err = future.result(timeout=120)
                    if err:
                        logger.warning("[kline_backfill][%s] failed: %s", symbol, err)
                        self._progress.mark_failed(symbol, err)
                        saved[symbol] = 0
                    else:
                        logger.info(
                            "[kline_backfill][%s] %d rows → DB (%s)",
                            symbol, rows, data_source,
                        )
                        self._progress.mark_done(symbol)
                        saved[symbol] = rows
                except Exception as exc:
                    logger.warning("[kline_backfill][%s] future failed: %s", sym, exc)
                    self._progress.mark_failed(sym, str(exc))
                    saved[sym] = 0

                completed += 1
                if completed % 10 == 0 or completed == total:
                    elapsed = time.time() - t0_all
                    rate = elapsed / completed if completed > 0 else 0
                    eta = rate * (total - completed) if rate > 0 else 0
                    logger.info(
                        "[kline_backfill] progress %d/%d (%.1f%%) elapsed=%.1fs eta=%.1fs",
                        completed, total, 100 * completed / total, elapsed, eta,
                    )
                    self._progress.save()

        self._progress.save()
        logger.info(
            "[kline_backfill] parallel %s done — %d symbols in %.1fs",
            data_source, len(to_process), time.time() - t0_all,
        )
        return saved

    # ── Legacy sequential batch (for small symbol sets) ───────────────────────

    def _fetch_and_save_sequential(
        self,
        symbols: list[str],
        period: str,
        data_source: str,
    ) -> dict[str, int]:
        """Sequential batch for small symbol sets or when parallel is disabled."""
        from v3_pipeline.data.yf_provider import download_history

        saved: dict[str, int] = {}
        n_batches = (len(symbols) + self._batch_size - 1) // self._batch_size

        for i in range(0, len(symbols), self._batch_size):
            batch = symbols[i : i + self._batch_size]
            batch_no = i // self._batch_size + 1
            t0 = time.time()

            hist_map = download_history(batch, period=period, interval="1d")

            for sym, hist in hist_map.items():
                if hist.empty:
                    logger.warning("[kline_backfill][%s] empty history — skipping", sym)
                    saved[sym] = 0
                    continue
                try:
                    ohlcv = _prepare_ohlcv(hist)
                    if ohlcv.empty:
                        saved[sym] = 0
                        continue

                    featured = self._indicator_gen.generate(ohlcv)
                    db_frame = _to_db_frame(featured, sym, data_source)

                    self._db.save_data(db_frame, symbol=sym)
                    saved[sym] = len(db_frame)
                    logger.info(
                        "[kline_backfill][%s] %d rows → DB (%s)",
                        sym, len(db_frame), data_source,
                    )
                except Exception as exc:
                    logger.warning("[kline_backfill][%s] failed: %s", sym, exc)
                    saved[sym] = 0

            logger.info(
                "[kline_backfill] batch %d/%d done in %.1fs",
                batch_no, n_batches, time.time() - t0,
            )
            if i + self._batch_size < len(symbols):
                time.sleep(self._cooldown_sec)

        return saved

    # ── Public API ────────────────────────────────────────────────────────────

    def run_smart_repair(self, symbols: list[str]) -> dict[str, int]:
        """
        Full 2-year backfill for symbols with <60 bars.
        Skips symbols that already have enough data.
        Uses parallel processing for >20 symbols.
        """
        needs = self.symbols_needing_backfill(symbols)
        if not needs:
            logger.info(
                "[kline_backfill] all %d symbols have ≥%d bars — repair skipped",
                len(symbols), MIN_BARS,
            )
            return {}
        logger.info(
            "[kline_backfill] smart repair: %d/%d symbols need backfill",
            len(needs), len(symbols),
        )

        # Use parallel for large batches
        if len(needs) > 20 and self._max_workers > 1:
            return self._fetch_and_save_parallel(needs, FULL_PERIOD, "YF_HIST")
        return self._fetch_and_save_sequential(needs, FULL_PERIOD, "YF_HIST")

    def run_incremental(self, symbols: list[str]) -> dict[str, int]:
        """Fetch last 5d of daily bars for all symbols (catches today's close)."""
        logger.info("[kline_backfill] incremental update: %d symbols", len(symbols))
        # Incremental is fast; use sequential to avoid overloading yfinance
        return self._fetch_and_save_sequential(symbols, INCREMENTAL_PERIOD, "YF_LIVE")

    def run_full_cycle(self, symbols: list[str]) -> tuple[dict[str, int], dict[str, int]]:
        """smart_repair then incremental — use this as the single idle entry point."""
        repair = self.run_smart_repair(symbols)
        incremental = self.run_incremental(symbols)
        return repair, incremental

    def reset_progress(self, symbols: Optional[list[str]] = None) -> None:
        """Reset backfill progress for symbols (or all)."""
        self._progress.reset(symbols)
