#!/usr/bin/env python3
"""Meta-labeling — dataset extractor (offline).

Milestone: M0.ohlcv_enrichment

Reads:
- learning/us_sim/account_snap_us_sim.jsonl
- learning/us_sim/decision_trace_us_sim.jsonl

Outputs:
- joined JSONL (optional): dev/meta_labeling/out/joined_events.jsonl
- enriched JSONL (optional): dev/meta_labeling/out/enriched_events.jsonl

Notes:
- This module is read-only and safe.
- It tolerates mixed schema variants and occasional malformed/concatenated JSON lines.
- OHLCV enrichment uses yfinance (1m bars) to get price/volume at decision time.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import yfinance as yf
except Exception:
    yf = None


ALLOWED_ACTION_PREFIXES = (
    "BUY_PLACED",
    "SELL_PLACED",
    "BUY_BLOCKED_",
    "BUY_SKIPPED_",
)


def _parse_iso_ts(value: str) -> datetime | None:
    try:
        s = str(value).strip()
        if not s:
            return None
        # Allow Z
        s = s.replace("Z", "+00:00")
        # If missing timezone, assume UTC
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _ts_from_obj(obj: dict[str, Any]) -> datetime | None:
    # Prefer explicit UTC field
    for k in ("ts_utc", "timestamp_utc", "ts", "timestamp"):
        if k in obj and obj[k]:
            dt = _parse_iso_ts(str(obj[k]))
            if dt:
                return dt.astimezone(timezone.utc)
    return None


def _safe_float(x: Any, default: float | None = None) -> float | None:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def _json_objects_from_line(line: str) -> list[dict[str, Any]]:
    """Parse 1 line into 0..N json objects.

    Tolerates:
    - a single JSON dict line
    - concatenated dicts without newline: {...}{...}
    - empty/garbage lines
    """
    s = line.strip()
    if not s:
        return []
    # Fast path
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return [obj]
        return []
    except Exception:
        pass

    # Attempt to split concatenated objects
    # Heuristic: insert delimiter between '}{'
    parts = s.replace("}{", "}\n{").splitlines()
    out: list[dict[str, Any]] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            obj = json.loads(p)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def iter_json_dicts(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    for line in path.read_text(errors="ignore").splitlines():
        for obj in _json_objects_from_line(line):
            yield obj


@dataclass
class AccountSnapshot:
    ts_utc: datetime
    total_assets: float | None
    cash: float | None
    market_val: float | None
    raw: dict[str, Any]


def normalize_account_snapshot(obj: dict[str, Any]) -> AccountSnapshot | None:
    ts = _ts_from_obj(obj)
    if ts is None:
        return None

    # schema variants
    total_assets = _safe_float(obj.get("total_assets"))
    if total_assets is None:
        total_assets = _safe_float(obj.get("assets"))

    cash = _safe_float(obj.get("cash"))
    if cash is None:
        cash = _safe_float(obj.get("cash_balance"))

    market_val = _safe_float(obj.get("market_val"))
    if market_val is None:
        market_val = _safe_float(obj.get("mv"))

    return AccountSnapshot(
        ts_utc=ts,
        total_assets=total_assets,
        cash=cash,
        market_val=market_val,
        raw=obj,
    )


def is_relevant_decision(obj: dict[str, Any]) -> bool:
    act = str(obj.get("action") or obj.get("event") or "").strip()
    if not act:
        return False
    return act.startswith(ALLOWED_ACTION_PREFIXES)


def build_snap_index(snaps: list[AccountSnapshot]) -> list[AccountSnapshot]:
    return sorted(snaps, key=lambda s: s.ts_utc)


def find_prev_snapshot(snaps_sorted: list[AccountSnapshot], ts: datetime) -> AccountSnapshot | None:
    # binary search
    lo, hi = 0, len(snaps_sorted)
    while lo < hi:
        mid = (lo + hi) // 2
        if snaps_sorted[mid].ts_utc <= ts:
            lo = mid + 1
        else:
            hi = mid
    idx = lo - 1
    if idx >= 0:
        return snaps_sorted[idx]
    return None


def join_decisions_to_prev_snapshot(
    decisions: list[dict[str, Any]],
    snaps_sorted: list[AccountSnapshot],
) -> list[dict[str, Any]]:
    joined: list[dict[str, Any]] = []
    for d in decisions:
        ts = _ts_from_obj(d)
        if ts is None:
            continue
        prev = find_prev_snapshot(snaps_sorted, ts)
        row = {
            "schema": "kiro.meta_labeling.joined.v1",
            "decision_ts_utc": ts.isoformat(timespec="seconds"),
            "action": d.get("action"),
            "symbol": d.get("symbol"),
            "bucket": d.get("bucket"),
            "px": d.get("px"),
            "prediction": d.get("prediction"),
            "confidence": d.get("confidence"),
            "threshold": d.get("threshold"),
            "sentiment_score": d.get("sentiment_score"),
            "gate": d.get("action"),
            "snapshot_ts_utc": prev.ts_utc.isoformat(timespec="seconds") if prev else None,
            "snapshot_total_assets": prev.total_assets if prev else None,
            "snapshot_cash": prev.cash if prev else None,
            "snapshot_market_val": prev.market_val if prev else None,
        }
        joined.append(row)
    return joined


def _fetch_ohlcv_at(symbol: str, dt: datetime, interval: str = "1m") -> dict[str, Any] | None:
    """Fetch OHLCV bar closest to dt using yfinance."""
    if yf is None:
        return None
    try:
        # Round down to minute start
        ts = dt.replace(second=0, microsecond=0)
        # Use start/end dates to get full trading day coverage
        # yfinance interprets dates as America/New_York, so use day-before/after to capture the session
        start_date = (ts - timedelta(days=1)).strftime("%Y-%m-%d")
        end_date = (ts + timedelta(days=1)).strftime("%Y-%m-%d")
        data = yf.download(symbol, start=start_date, end=end_date, interval=interval, auto_adjust=False, progress=False)
        if data.empty:
            return None
        target = dt
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        target_utc = target.astimezone(timezone.utc)

        def _time_diff(idx_ts):
            idx_dt = idx_ts.to_pydatetime()
            if idx_dt.tzinfo is None:
                idx_dt = idx_dt.replace(tzinfo=timezone.utc)
            else:
                idx_dt = idx_dt.astimezone(timezone.utc)
            return abs((idx_dt - target_utc).total_seconds())

        closest_index = min(data.index, key=_time_diff)
        row = data.loc[closest_index]
        # Handle multi-index columns if present
        return {
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"]),
        }
    except Exception as e:
        print(f"yfinance error at {symbol}: {e}")
        return None


def _fetch_ohlcv_bars(symbol: str, dt: datetime, lookback: int = 60, interval: str = "5m") -> list[dict[str, Any]]:
    """Fetch recent OHLCV bars immediately preceding dt for indicator computation."""
    if yf is None:
        return []
    try:
        ts = dt.replace(second=0, microsecond=0)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        target_utc = ts.astimezone(timezone.utc)

        # Use start/end date range to capture full trading session
        end_date = (ts + timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = (ts - timedelta(days=2)).strftime("%Y-%m-%d")  # Go back 2 days to ensure coverage
        data = yf.download(symbol, start=start_date, end=end_date, interval=interval, auto_adjust=False, progress=False)
        if data.empty:
            return []

        idx = data.index
        tz = getattr(idx, "tz", None)
        if tz is None:
            idx = idx.tz_localize(timezone.utc)
        else:
            idx = idx.tz_convert(timezone.utc)
        data = data.copy()
        data.index = idx

        prior_data = data[data.index <= target_utc]
        if prior_data.empty:
            return []

        window = prior_data.tail(lookback)
        records: list[dict[str, Any]] = []
        for _, row in window.iterrows():
            records.append({
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
            })
        return records
    except Exception as e:
        print(f"yfinance bars error: {e}")
        return []


def _compute_indicators(closes: list[float]) -> dict[str, float | None]:
    """Compute technical indicators from close prices."""
    if len(closes) < 5:
        return {"sma_5": None, "sma_20": None, "rsi_14": None, "macd": None, "macd_signal": None, "bb_upper": None, "bb_middle": None, "bb_lower": None}

    import statistics

    # SMA
    sma_5 = statistics.mean(closes[-5:]) if len(closes) >= 5 else None
    sma_20 = statistics.mean(closes[-20:]) if len(closes) >= 20 else None

    # RSI (simple implementation)
    def rsi14(prices: list[float]) -> float | None:
        if len(prices) < 15:
            return None
        gains, losses = [], []
        for i in range(1, min(15, len(prices))):
            delta = prices[i] - prices[i - 1]
            if delta > 0:
                gains.append(delta)
            else:
                losses.append(abs(delta))
        if not gains or not losses:
            return None
        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    rsi_14 = rsi14(closes)

    # MACD (12, 26, 9)
    ema12 = sum(closes[-12:]) / 12 if len(closes) >= 12 else None
    ema26 = sum(closes[-26:]) / 26 if len(closes) >= 26 else None
    macd = (ema12 - ema26) if (ema12 and ema26) else None
    # Simple signal (9-period EMA of MACD, approximated)
    macd_signal = None

    # Bollinger Bands (20-period, 2 std)
    if len(closes) >= 20:
        middle = statistics.mean(closes[-20:])
        stdev = statistics.stdev(closes[-20:])
        bb_upper = middle + 2 * stdev
        bb_lower = middle - 2 * stdev
    else:
        middle, bb_upper, bb_lower = None, None, None

    return {
        "sma_5": sma_5,
        "sma_20": sma_20,
        "rsi_14": rsi_14,
        "macd": macd,
        "macd_signal": macd_signal,
        "bb_upper": bb_upper,
        "bb_middle": middle,
        "bb_lower": bb_lower,
    }


def enrich_with_ohlcv(
    joined_rows: list[dict[str, Any]],
    ohlcv_cache: dict[str, dict[datetime, dict[str, Any]]],
    bars_cache: dict[str, dict[datetime, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """Add OHLCV at decision time + technical indicators to each joined row."""
    enriched = []
    for row in joined_rows:
        sym = row.get("symbol")
        dec_ts_str = row.get("decision_ts_utc")
        if not sym or not dec_ts_str:
            enriched.append(row)
            continue
        # Parse decision timestamp
        try:
            dec_ts = datetime.fromisoformat(dec_ts_str.replace("Z", "+00:00"))
        except Exception:
            enriched.append(row)
            continue

        new_row = dict(row)

        # Single-bar OHLCV
        if sym not in ohlcv_cache:
            ohlcv_cache[sym] = {}
        if dec_ts in ohlcv_cache[sym]:
            ohlcv = ohlcv_cache[sym][dec_ts]
        else:
            ohlcv = _fetch_ohlcv_at(sym, dec_ts)
            ohlcv_cache[sym][dec_ts] = ohlcv

        if ohlcv:
            new_row["ohlcv_open"] = ohlcv.get("open")
            new_row["ohlcv_high"] = ohlcv.get("high")
            new_row["ohlcv_low"] = ohlcv.get("low")
            new_row["ohlcv_close"] = ohlcv.get("close")
            new_row["ohlcv_volume"] = ohlcv.get("volume")
        else:
            new_row["ohlcv_error"] = True

        # Indicator computation (5m bars lookback)
        if sym not in bars_cache:
            bars_cache[sym] = {}
        if dec_ts in bars_cache[sym]:
            bars = bars_cache[sym][dec_ts]
        else:
            bars = _fetch_ohlcv_bars(sym, dec_ts, lookback=60, interval="5m")
            bars_cache[sym][dec_ts] = bars

        if bars:
            closes = [b["close"] for b in bars if "close" in b]
            if closes:
                inds = _compute_indicators(closes)
                for k, v in inds.items():
                    new_row[f"ind_{k}"] = v
        else:
            new_row["ind_error"] = True

        enriched.append(new_row)
    return enriched


def cmd_dry_run(snap_path: Path, dec_path: Path) -> int:
    snaps = [s for s in (normalize_account_snapshot(o) for o in iter_json_dicts(snap_path)) if s]
    dec = [o for o in iter_json_dicts(dec_path) if is_relevant_decision(o)]
    print(f"account_snapshots: {len(snaps)}")
    print(f"decision_events: {len(dec)}")
    return 0


def cmd_join(snap_path: Path, dec_path: Path, out_path: Path) -> int:
    snaps = [s for s in (normalize_account_snapshot(o) for o in iter_json_dicts(snap_path)) if s]
    snaps_sorted = build_snap_index(snaps)
    dec = [o for o in iter_json_dicts(dec_path) if is_relevant_decision(o)]
    joined = join_decisions_to_prev_snapshot(dec, snaps_sorted)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for row in joined:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"joined_rows: {len(joined)} -> {out_path}")
    return 0


def cmd_enrich(joined_path: Path, out_path: Path) -> int:
    if not joined_path.exists():
        print(f"Error: joined file not found: {joined_path}")
        return 1
    rows = [json.loads(line) for line in joined_path.read_text().splitlines() if line.strip()]
    if not rows:
        print("No joined rows to enrich.")
        return 0
    ohlcv_cache: dict[str, dict[datetime, dict[str, Any]]] = {}
    bars_cache: dict[str, dict[datetime, list[dict[str, Any]]]] = {}
    enriched = enrich_with_ohlcv(rows, ohlcv_cache, bars_cache)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for row in enriched:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"enriched_rows: {len(enriched)} -> {out_path}")
    print(f"unique_symbols_ohlcv: {len(ohlcv_cache)}")
    print(f"unique_symbols_bars: {len(bars_cache)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--snap",
        default="learning/us_sim/account_snap_us_sim.jsonl",
        help="account snapshot JSONL",
    )
    ap.add_argument(
        "--decisions",
        default="learning/us_sim/decision_trace_us_sim.jsonl",
        help="decision trace JSONL",
    )
    ap.add_argument(
        "--out",
        default="dev/meta_labeling/out/joined_events.jsonl",
        help="output joined JSONL",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--join", action="store_true")
    ap.add_argument("--enrich", action="store_true")
    ap.add_argument(
        "--joined",
        default=None,
        help="input joined JSONL for enrichment step",
    )
    args = ap.parse_args()

    snap_path = Path(args.snap)
    dec_path = Path(args.decisions)
    out_path = Path(args.out)

    if args.enrich:
        # use --out as input if no explicit --joined option
        joined_path = out_path
        # allow override input path
        if args.joined:
            joined_path = Path(args.joined)
        return cmd_enrich(joined_path, out_path)
    if args.dry_run or (not args.join):
        return cmd_dry_run(snap_path, dec_path)
    return cmd_join(snap_path, dec_path, out_path)


if __name__ == "__main__":
    raise SystemExit(main())
