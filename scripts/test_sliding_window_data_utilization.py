#!/usr/bin/env python3
"""Validate DataPreparer sliding-window sample utilization."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from v3_pipeline.models.manager import DataPreparer


def build_synthetic_frame(rows: int) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    base = np.linspace(100, 140, rows)
    noise = rng.normal(0, 0.5, rows)
    close = base + noise
    open_ = close + rng.normal(0, 0.2, rows)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.3, rows))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.3, rows))
    volume = rng.integers(100_000, 500_000, rows)

    return pd.DataFrame(
        {
            "Date": pd.date_range("2020-01-01", periods=rows, freq="min"),
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate sliding-window sample count increase")
    parser.add_argument("--rows", type=int, default=272_698)
    parser.add_argument("--lookback", type=int, default=7_776)
    args = parser.parse_args()

    frame = build_synthetic_frame(args.rows)
    preparer = DataPreparer(lookback=args.lookback, target_col="Close")

    x_legacy, y_legacy = preparer.fit_transform(frame, sliding_window=False)
    x_sliding, y_sliding = preparer.fit_transform(frame, sliding_window=True)

    usable = args.rows - args.lookback
    expected_legacy = len(range(args.lookback, args.rows, args.lookback))
    expected_sliding = usable

    print(f"rows={args.rows}, lookback={args.lookback}, usable_points={usable}")
    print(f"legacy samples={len(x_legacy)} (expected {expected_legacy})")
    print(f"sliding samples={len(x_sliding)} (expected {expected_sliding})")
    print(f"x shapes legacy={tuple(x_legacy.shape)} sliding={tuple(x_sliding.shape)}")
    print(f"y shapes legacy={tuple(y_legacy.shape)} sliding={tuple(y_sliding.shape)}")

    utilization_legacy = len(x_legacy) / usable
    utilization_sliding = len(x_sliding) / usable
    ratio = len(x_sliding) / max(1, len(x_legacy))

    print(f"utilization legacy={utilization_legacy:.6%}")
    print(f"utilization sliding={utilization_sliding:.6%}")
    print(f"sample increase ratio={ratio:.2f}x")

    assert len(x_legacy) == expected_legacy
    assert len(x_sliding) == expected_sliding
    assert len(y_legacy) == expected_legacy
    assert len(y_sliding) == expected_sliding


if __name__ == "__main__":
    main()
