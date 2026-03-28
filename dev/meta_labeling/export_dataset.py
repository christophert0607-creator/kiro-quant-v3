#!/usr/bin/env python3
"""Persist enriched meta-labeling events to a parquet dataset.

Milestone: M0.dataset_persistence

Reads:
- dev/meta_labeling/out/enriched_events.jsonl

Produces:
- dev/meta_labeling/datasets/us_sim_YYYY-MM-DD.parquet
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None

DEFAULT_INPUT = Path("dev/meta_labeling/out/enriched_events.jsonl")
DEFAULT_OUT_DIR = Path("dev/meta_labeling/datasets")


def _parse_iso_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # pragma: no cover
        return None


def _flatten_record(record: dict[str, Any]) -> dict[str, Any]:
    out = dict(record)
    labels = out.get("labels")
    if isinstance(labels, dict):
        for label_key, label_value in labels.items():
            out[f"labels_{label_key}"] = label_value
    return out


def load_records(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"input path not found: {path}")
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if limit and len(records) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            records.append(_flatten_record(data))
    return records


def _infer_dataset_date(records: list[dict[str, Any]]) -> datetime.date:
    dates: list[datetime.date] = []
    for record in records:
        ts = _parse_iso_ts(record.get("decision_ts_utc"))
        if ts:
            dates.append(ts.date())
    if dates:
        return max(dates)
    return datetime.now(timezone.utc).date()


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist enriched meta-labeling events into Parquet")
    parser.add_argument("--input", "-i", type=Path, default=DEFAULT_INPUT, help="enriched JSONL to read")
    parser.add_argument("--out-dir", "-o", type=Path, default=DEFAULT_OUT_DIR, help="directory for parquet datasets")
    parser.add_argument("--dataset-date", "-d", default=None, help="date for filename (YYYY-MM-DD). defaults to max(decision_ts_utc)")
    parser.add_argument("--limit", "-L", type=int, default=None, help="limit records for quick testing")
    parser.add_argument("--force", "-f", action="store_true", help="overwrite existing parquet file")
    args = parser.parse_args()

    if pd is None:
        print("Error: pandas is required to write a parquet dataset.")
        return 1

    input_path = args.input
    try:
        records = load_records(input_path, limit=args.limit)
    except FileNotFoundError as exc:
        print(exc)
        return 1

    if not records:
        print("No enriched events found to persist.")
        return 0

    dataset_date = args.dataset_date
    if dataset_date is None:
        inferred = _infer_dataset_date(records)
        dataset_date = inferred.isoformat()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"us_sim_{dataset_date}.parquet"
    if out_path.exists() and not args.force:
        print(f"Dataset already exists at {out_path}. Use --force to overwrite.")
        return 1

    df = pd.DataFrame(records)
    if "decision_ts_utc" in df.columns:
        df["decision_ts_utc"] = pd.to_datetime(df["decision_ts_utc"], errors="coerce", utc=True)

    df.to_parquet(out_path, index=False)

    print(f"Persisted {len(df)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
