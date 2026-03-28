#!/usr/bin/env python3
"""Diagnostics for the baseline meta-labeling dataset."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

FEATURE_KEYS = [
    "confidence",
    "snapshot_total_assets",
    "snapshot_cash",
    "snapshot_market_val",
]

OHLCV_KEYS = ["ohlcv_open", "ohlcv_high", "ohlcv_low", "ohlcv_close", "ohlcv_volume"]
INDICATOR_KEYS = [
    "ind_sma_5",
    "ind_sma_20",
    "ind_rsi_14",
    "ind_macd",
    "ind_bb_upper",
    "ind_bb_lower",
]

DIAGNOSTICS_SCHEMA = "kiro.meta_labeling.baseline_diagnostics.v1"


def load_events(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        raise FileNotFoundError(f"Labeled events file not found: {path}")
    for line in path.read_text(errors="ignore").splitlines():
        if limit and len(events) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(events)
    label_counter: Counter[int] = Counter()
    horizon_set: set[str] = set()
    features = Counter()
    symbol_counts: Counter[str] = Counter()
    ohlcv_hits = 0
    indicator_hits = 0

    for event in events:
        symbol = event.get("symbol")
        if symbol:
            symbol_counts[str(symbol)] += 1

        label = event.get("label")
        if isinstance(label, (int, float)):
            label_counter[int(label)] += 1

        labels = event.get("labels")
        if isinstance(labels, dict):
            for key in labels:
                if key.startswith("label_"):
                    horizon_set.add(key.replace("label_", ""))

        if all(event.get(k) is not None for k in OHLCV_KEYS):
            ohlcv_hits += 1

        if any(event.get(k) is not None for k in INDICATOR_KEYS):
            indicator_hits += 1

        for key in FEATURE_KEYS + OHLCV_KEYS + INDICATOR_KEYS:
            if event.get(key) is not None:
                features[key] += 1

    labeled = sum(label_counter.values())
    summary: dict[str, Any] = {
        "schema": DIAGNOSTICS_SCHEMA,
        "total_events": total,
        "labeled_events": labeled,
        "label_distribution": {str(k): v for k, v in sorted(label_counter.items())},
        "label_horizons": sorted(horizon_set),
        "symbols": [
            {"symbol": sym, "count": cnt} for sym, cnt in symbol_counts.most_common(5)
        ],
        "ohlcv": {
            "with_all_fields": ohlcv_hits,
            "missing": total - ohlcv_hits,
        },
        "indicators": {
            "with_any": indicator_hits,
            "missing": total - indicator_hits,
        },
        "feature_counts": {key: features.get(key, 0) for key in FEATURE_KEYS + OHLCV_KEYS + INDICATOR_KEYS},
    }

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Describe baseline dataset health")
    parser.add_argument("--input", "-i", default="dev/meta_labeling/out/labeled_events.jsonl",
                        help="Labeled events JSONL")
    parser.add_argument("--out", "-o", default="dev/meta_labeling/out/baseline_diagnostics.json",
                        help="Diagnostics JSON output")
    parser.add_argument("--limit", "-L", type=int, default=None,
                        help="Limit the number of events to scan")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.out)
    events = load_events(input_path, limit=args.limit)
    if not events:
        print("No events found; diagnostics skipped.")
        return 0

    summary = summarize(events)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    print(f"Processed {len(events)} events")
    print(f"Diagnostics schema: {summary['schema']}")
    print(f"Written diagnostics to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
