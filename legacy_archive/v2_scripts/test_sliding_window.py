#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from v3_pipeline.models.manager import DataPreparer, ModelManager


def _count_windows(num_rows: int, lookback: int, stride: int) -> int:
    if num_rows <= lookback:
        return 0
    return len(range(lookback, num_rows, stride))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--storage-dir",
        default="v3_pipeline/data/storage/base_10y",
        help="Path to the base_10y parquet directory",
    )
    parser.add_argument("--lookback", type=int, default=40)
    parser.add_argument(
        "--materialize",
        action="store_true",
        help="Build tensors for a single symbol to validate exact shapes.",
    )
    args = parser.parse_args()

    storage_dir = Path(args.storage_dir)
    frames = ModelManager.load_base_10y_frames(storage_dir)
    if not frames:
        raise SystemExit(f"No parquet data found under {storage_dir}")

    preparer = DataPreparer(lookback=args.lookback, target_col="Close")

    total_rows = 0
    before = 0
    after = 0
    skipped = 0

    for symbol, frame in frames.items():
        try:
            clean = preparer._sanitize(frame)
        except Exception:
            skipped += 1
            continue
        rows = len(clean)
        total_rows += rows
        before += _count_windows(rows, args.lookback, args.lookback)
        after += _count_windows(rows, args.lookback, 1)

    ratio = (after / max(1, before)) if before else float("inf")

    print("Sliding Window Sample Count Check")
    print(f"Symbols processed: {len(frames)}")
    print(f"Symbols skipped: {skipped}")
    print(f"Rows after sanitize: {total_rows}")
    print(f"Lookback: {args.lookback}")
    print(f"Before (stride=lookback): {before}")
    print(f"After  (stride=1): {after}")
    print(f"Improvement: {ratio:.1f}x")

    if args.materialize:
        sample_symbol = next(iter(frames))
        sample_frame = frames[sample_symbol]
        print(f"\nMaterializing tensors for {sample_symbol}...")
        x_before, y_before = preparer.fit_transform(sample_frame, sliding_window=False)
        x_after, y_after = preparer.fit_transform(sample_frame, sliding_window=True)
        print(f"Tensor before: X={tuple(x_before.shape)} y={tuple(y_before.shape)}")
        print(f"Tensor after:  X={tuple(x_after.shape)} y={tuple(y_after.shape)}")


if __name__ == "__main__":
    main()
