#!/usr/bin/env python3
"""Summarize labeled events for meta-labeling and record split rules."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_METADATA_PATH = Path("dev/meta_labeling/out/label_metadata.json")
DEFAULT_SPLITS_PATH = Path("dev/meta_labeling/out/label_splits.json")
DEFAULT_INPUT_PATH = Path("dev/meta_labeling/out/labeled_events.jsonl")


def _parse_iso_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        ts = str(value).strip()
        if not ts:
            return None
        ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _build_event_id(index: int, event: dict[str, Any]) -> str:
    symbol = event.get("symbol") or "unknown"
    ts = event.get("decision_ts_utc") or "unknown"
    return f"{index:04d}-{symbol}-{ts}"


def _iter_events(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r") as fp:
        for idx, line in enumerate(fp):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            data.setdefault("_meta", {})
            data["_meta"]["index"] = idx
            data["_meta"]["event_id"] = _build_event_id(idx, data)
            data["_meta"]["decision_dt"] = _parse_iso_ts(data.get("decision_ts_utc"))
            yield data


def build_metadata(events: list[dict[str, Any]]) -> dict[str, Any]:
    label_counts: dict[int | str, int] = defaultdict(int)
    horizon_stats: dict[int, dict[str, Any]] = {}
    processed = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    for event in events:
        processed += 1

        decision_dt = event.get("_meta", {}).get("decision_dt")
        if decision_dt:
            if first_ts is None or decision_dt < first_ts:
                first_ts = decision_dt
            if last_ts is None or decision_dt > last_ts:
                last_ts = decision_dt

        label = event.get("label")
        if label is not None:
            label_counts[label] += 1

        label_horizons: Iterable[int] = event.get("label_horizons") or []
        labels = event.get("labels") or {}

        for horizon in label_horizons:
            stats = horizon_stats.setdefault(
                horizon,
                {
                    "horizon_minutes": horizon,
                    "samples": 0,
                    "positive": 0,
                    "negative": 0,
                    "return_sum": 0.0,
                    "return_min": None,
                    "return_max": None,
                },
            )

            fwd_key = f"fwd_return_{horizon}m"
            label_key = f"label_{horizon}m"
            fwd_value = labels.get(fwd_key)
            label_value = labels.get(label_key)

            if fwd_value is not None:
                stats["samples"] += 1
                stats["return_sum"] += fwd_value
                stats["return_min"] = fwd_value if stats["return_min"] is None else min(stats["return_min"], fwd_value)
                stats["return_max"] = fwd_value if stats["return_max"] is None else max(stats["return_max"], fwd_value)

            if label_value == 1:
                stats["positive"] += 1
            elif label_value == 0:
                stats["negative"] += 1

    # finalize horizon stats
    for stats in horizon_stats.values():
        samples = stats.get("samples", 0)
        if samples:
            stats["return_mean"] = round(stats["return_sum"] / samples, 6)
        else:
            stats["return_mean"] = None
        stats.pop("return_sum", None)

        total_labels = stats["positive"] + stats["negative"]
        if total_labels:
            stats["positive_rate"] = round(stats["positive"] / total_labels, 4)
        else:
            stats["positive_rate"] = None

    metadata: dict[str, Any] = {
        "total_events": processed,
        "label_counts": dict(label_counts),
        "decision_ts_range": {
            "first": first_ts.isoformat() if first_ts else None,
            "last": last_ts.isoformat() if last_ts else None,
        },
        "horizon_stats": {str(k): v for k, v in sorted(horizon_stats.items())},
    }

    if processed > 0:
        metadata["available_labels"] = sorted(set(label_counts.keys()))

    return metadata


def compute_splits(events: list[dict[str, Any]], train_ratio: float, val_ratio: float, test_ratio: float) -> dict[str, Any]:
    ordered = sorted(
        events,
        key=lambda ev: ev.get("_meta", {}).get("decision_dt") or datetime.fromtimestamp(0, tz=timezone.utc),
    )
    total = len(ordered)
    if total == 0:
        return {"rules": {}, "splits": {"train": [], "val": [], "test": []}, "counts": {"train": 0, "val": 0, "test": 0}}

    train_count = int(total * train_ratio)
    val_count = int(total * val_ratio)
    test_count = int(total * test_ratio)
    remainder = total - (train_count + val_count + test_count)
    train_count += remainder

    splits: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    counts = {"train": train_count, "val": val_count, "test": test_count}

    cursor = 0
    for bucket, size in (("train", train_count), ("val", val_count), ("test", test_count)):
        for _ in range(size):
            if cursor >= total:
                break
            splits[bucket].append(ordered[cursor]["_meta"]["event_id"])
            cursor += 1

    return {
        "rules": {
            "train_ratio": train_ratio,
            "val_ratio": val_ratio,
            "test_ratio": test_ratio,
        },
        "counts": counts,
        "splits": splits,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Describe labeled events and capture split assignments")
    parser.add_argument("--input", "-i", type=Path, default=DEFAULT_INPUT_PATH, help="Labeled events JSONL")
    parser.add_argument("--metadata", "-m", type=Path, default=DEFAULT_METADATA_PATH, help="Metadata output path")
    parser.add_argument("--splits", "-s", type=Path, default=DEFAULT_SPLITS_PATH, help="Split assignment output path")
    parser.add_argument("--train-ratio", type=float, default=0.7, help="Training set ratio")
    parser.add_argument("--val-ratio", type=float, default=0.15, help="Validation set ratio")
    parser.add_argument("--test-ratio", type=float, default=0.15, help="Test set ratio")

    args = parser.parse_args()

    events = list(_iter_events(args.input))
    metadata = build_metadata(events)
    metadata["split_rule_summary"] = {
        "train_ratio": args.train_ratio,
        "val_ratio": args.val_ratio,
        "test_ratio": args.test_ratio,
    }

    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.splits.parent.mkdir(parents=True, exist_ok=True)

    args.metadata.write_text(json.dumps(metadata, indent=2))
    splits = compute_splits(events, args.train_ratio, args.val_ratio, args.test_ratio)
    args.splits.write_text(json.dumps(splits, indent=2))

    print(f"Indexed {len(events)} labeled events")
    print(f"Metadata written to {args.metadata}")
    print(f"Splits written to {args.splits}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
