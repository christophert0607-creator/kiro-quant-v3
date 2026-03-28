#!/usr/bin/env python3
"""Meta-labeling - label generator (offline).

Milestone: M1.labeling

Reads:
- dev/meta_labeling/out/enriched_events.jsonl (from M0)

Outputs:
- dev/meta_labeling/out/labeled_events.jsonl with forward-return labels

Labels:
- fwd_return_{horizon}: percentage return from entry to horizon
- label: 1 if fwd_return exceeds the configured horizon threshold (default 0)
- label_thresholds: per-horizon thresholds saved alongside label metadata
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import yfinance as yf
except Exception:
    yf = None


DEFAULT_HORIZONS = [30, 60]  # minutes
DEFAULT_THRESHOLD = 0.0

# Cache yfinance forward-close lookups to avoid redundant downloads during labeling runs
_FETCH_CLOSE_CACHE: dict[tuple[str, str, int], float | None] = {}


def _parse_iso_ts(value: str) -> datetime | None:
    try:
        s = str(value).strip()
        if not s:
            return None
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _fetch_future_close(symbol: str, decision_ts: datetime, horizon_minutes: int) -> float | None:
    """Fetch the close price at decision_ts + horizon_minutes."""
    target_ts = decision_ts + timedelta(minutes=horizon_minutes)
    cache_key = (symbol, target_ts.isoformat(), horizon_minutes)

    if cache_key in _FETCH_CLOSE_CACHE:
        return _FETCH_CLOSE_CACHE[cache_key]

    if yf is None:
        _FETCH_CLOSE_CACHE[cache_key] = None
        return None

    # yfinance window: start 2 days before target, end 1 day after target
    # This ensures coverage for after-market decisions
    start = target_ts - timedelta(days=2)
    end = target_ts + timedelta(days=1)

    try:
        # Use yf.download for clean API with expanded date range
        df = yf.download(
            symbol,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="5m",
            progress=False,
        )
        if df.empty:
            _FETCH_CLOSE_CACHE[cache_key] = None
            return None

        # Find closest bar at or after target_ts
        df = df.reset_index()
        df["datetime"] = df["Datetime"].dt.tz_localize(None)
        target_naive = target_ts.replace(tzinfo=None)

        # Filter to >= target
        candidates = df[df["datetime"] >= target_naive]
        if candidates.empty:
            _FETCH_CLOSE_CACHE[cache_key] = None
            return None

        result = float(candidates.iloc[0]["Close"])
        _FETCH_CLOSE_CACHE[cache_key] = result
        return result
    except Exception:
        _FETCH_CLOSE_CACHE[cache_key] = None
        return None


def _compute_fwd_return(entry_px: float, exit_px: float | None) -> float | None:
    if entry_px is None or entry_px <= 0 or exit_px is None:
        return None
    return (exit_px - entry_px) / entry_px


def _parse_thresholds(threshold_arg: str, horizons: list[int]) -> dict[int, float]:
    raw = [part.strip() for part in threshold_arg.split(",") if part.strip()]
    if not raw:
        raw = [str(DEFAULT_THRESHOLD)]

    thresholds = []
    for part in raw:
        thresholds.append(float(part))

    if len(thresholds) == 1:
        thresholds *= len(horizons)
    elif len(thresholds) != len(horizons):
        raise ValueError(
            "Provide either one threshold or one per horizon."
        )

    return dict(zip(horizons, thresholds))


def add_labels(events_path: str, horizons: list[int] = None, out_path: str = None,
               limit: int = None, thresholds: dict[int, float] | None = None) -> int:
    """Add forward-return labels to enriched events."""
    if horizons is None:
        horizons = DEFAULT_HORIZONS

    if thresholds is None:
        thresholds = {h: DEFAULT_THRESHOLD for h in horizons}

    if out_path is None:
        out_path = events_path.replace(".jsonl", "_labeled.jsonl")

    count = 0
    labeled_count = 0

    with open(events_path, "r") as f:
        for line in f:
            if limit and count >= limit:
                break

            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            count += 1

            # Get entry price and timestamp
            entry_px = event.get("ohlcv_close") or event.get("px")
            if not entry_px:
                continue

            decision_ts = _parse_iso_ts(event.get("decision_ts_utc"))
            if not decision_ts:
                continue

            symbol = event.get("symbol")
            if not symbol:
                continue

            # Compute forward returns for each horizon
            labels = {}
            for h in horizons:
                # Try to get future price
                # For BUY signals: forward return is from entry to future
                # For SELL signals: could treat as closing short
                future_px = _fetch_future_close(symbol, decision_ts, h)
                fwd_ret = _compute_fwd_return(entry_px, future_px)
                threshold = thresholds.get(h, DEFAULT_THRESHOLD)

                label_key = f"fwd_return_{h}m"
                if fwd_ret is not None:
                    labels[label_key] = round(fwd_ret, 6)
                    labels[f"label_{h}m"] = 1 if fwd_ret > threshold else 0
                else:
                    labels[label_key] = None
                    labels[f"label_{h}m"] = None

            composite_h = 30 if 30 in horizons else horizons[0]
            composite_key = f"label_{composite_h}m"
            if labels.get(composite_key) is not None:
                event["label"] = labels[composite_key]
                event["label_threshold"] = thresholds.get(composite_h, DEFAULT_THRESHOLD)

            # Add threshold metadata
            event["label_thresholds"] = {f"{h}m": thresholds.get(h, DEFAULT_THRESHOLD)
                                          for h in horizons}

            # Add all label fields
            event["labels"] = labels
            event["label_horizons"] = horizons

            labeled_count += 1

            # Write output
            with open(out_path, "a") as out_f:
                out_f.write(json.dumps(event) + "\n")

    print(f"Processed {count} events, labeled {labeled_count}")
    print(f"Output: {out_path}")
    return labeled_count


def summarize_labels(labeled_path: str) -> dict[str, Any]:
    """Summarize label distribution."""
    total = 0
    label_counts = {}
    fwd_returns = []
    thresholds_info: dict[str, float] | None = None

    with open(labeled_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            total += 1

            if thresholds_info is None:
                meta_thresholds = event.get("label_thresholds")
                if isinstance(meta_thresholds, dict):
                    thresholds_info = meta_thresholds.copy()

            label = event.get("label")
            if label is not None:
                label_counts[label] = label_counts.get(label, 0) + 1

            labels = event.get("labels", {})
            fwd_30m = labels.get("fwd_return_30m")
            if fwd_30m is not None:
                fwd_returns.append(fwd_30m)

    summary = {
        "total": total,
        "label_distribution": label_counts,
    }

    if thresholds_info:
        summary["label_thresholds"] = thresholds_info

    if fwd_returns:
        summary["fwd_return_stats"] = {
            "mean": round(sum(fwd_returns) / len(fwd_returns), 6),
            "min": round(min(fwd_returns), 6),
            "max": round(max(fwd_returns), 6),
        }
        if label_counts:
            win_rate = label_counts.get(1, 0) / total
            summary["win_rate_30m"] = round(win_rate, 4)

    return summary


def main():
    parser = argparse.ArgumentParser(description="Add forward-return labels to enriched events")
    parser.add_argument("--input", "-i", default="dev/meta_labeling/out/enriched_events.jsonl",
                        help="Input enriched events JSONL")
    parser.add_argument("--out", "-o", default="dev/meta_labeling/out/labeled_events.jsonl",
                        help="Output labeled events JSONL")
    parser.add_argument("--horizons", "-H", default="30,60",
                        help="Comma-separated horizons in minutes (default: 30,60)")
    parser.add_argument("--thresholds", "-t", default="0.0",
                        help="Comma-separated return thresholds aligned with horizons (default: 0.0)")
    parser.add_argument("--limit", "-L", type=int, default=None,
                        help="Limit number of events to process")
    parser.add_argument("--summarize", "-s", action="store_true",
                        help="Summarize label distribution after labeling")

    args = parser.parse_args()

    horizons = [int(h.strip()) for h in args.horizons.split(",")]
    try:
        threshold_map = _parse_thresholds(args.thresholds, horizons)
    except ValueError as exc:
        parser.error(str(exc))

    # Clear output file
    Path(args.out).unlink(missing_ok=True)

    labeled = add_labels(args.input, horizons, args.out, args.limit, thresholds=threshold_map)

    if args.summarize and labeled > 0:
        summary = summarize_labels(args.out)
        print("\n=== Label Summary ===")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()