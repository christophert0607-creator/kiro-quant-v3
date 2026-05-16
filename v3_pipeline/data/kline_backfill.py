"""
KlineBackfill — historical K-line data backfill and maintenance for kiro_quant.db.

Responsibilities:
  - Initial full backfill for symbols missing ≥MIN_BARS daily bars
  - Incremental daily updates (last 5d) for all watchlist symbols
  - Proper data_source tagging: "YF_HIST" (backfill) / "YF_LIVE" (incremental)
  - Timezone-normalised timestamps (UTC naive, no offset suffix)

Integration point: called from IdleTaskScheduler Task 4 via asyncio.to_thread.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger("kiro.kline_backfill")

_REPO_ROOT = Path(__file__).parent.parent.parent
_DB_PATH = _REPO_ROOT / "kiro_quant.db"

MIN_BARS = 60
FULL_PERIOD = "2y"
INCREMENTAL_PERIOD = "5d"
_DEFAULT_BATCH_SIZE = 10
_DEFAULT_COOLDOWN_SEC = 2.0


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

    # ── Health check ──────────────────────────────────────────────────────────

    def symbol_health(self, symbols: list[str]) -> dict[str, dict]:
        return self._db.check_health(symbols)

    def symbols_needing_backfill(self, symbols: list[str]) -> list[str]:
        health = self.symbol_health(symbols)
        return [s for s in symbols if (health.get(s) or {}).get("count", 0) < MIN_BARS]

    # ── Core fetch + save ─────────────────────────────────────────────────────

    def _fetch_and_save(
        self,
        symbols: list[str],
        period: str,
        data_source: str,
    ) -> dict[str, int]:
        """
        For each batch: download → compute TA → save.
        Returns {symbol: rows_saved}.
        """
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
        return self._fetch_and_save(needs, FULL_PERIOD, "YF_HIST")

    def run_incremental(self, symbols: list[str]) -> dict[str, int]:
        """Fetch last 5d of daily bars for all symbols (catches today's close)."""
        logger.info("[kline_backfill] incremental update: %d symbols", len(symbols))
        return self._fetch_and_save(symbols, INCREMENTAL_PERIOD, "YF_LIVE")

    def run_full_cycle(self, symbols: list[str]) -> tuple[dict[str, int], dict[str, int]]:
        """smart_repair then incremental — use this as the single idle entry point."""
        repair = self.run_smart_repair(symbols)
        incremental = self.run_incremental(symbols)
        return repair, incremental
