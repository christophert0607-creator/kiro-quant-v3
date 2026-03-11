#!/usr/bin/env python3
"""Dependency-free verification for sliding-window sample counts."""

from pathlib import Path

ROWS = 272_698
LOOKBACK = 7_776

usable = ROWS - LOOKBACK
legacy = len(range(LOOKBACK, ROWS, LOOKBACK))
sliding = len(range(LOOKBACK, ROWS, 1))

print(f"rows={ROWS} lookback={LOOKBACK} usable={usable}")
print(f"legacy_samples={legacy}")
print(f"sliding_samples={sliding}")
print(f"ratio={sliding/legacy:.2f}x")
print(f"legacy_utilization={legacy/usable:.6%}")
print(f"sliding_utilization={sliding/usable:.6%}")

source = Path('v3_pipeline/models/manager.py').read_text(encoding='utf-8')
needle = 'stride = 1 if sliding_window else self.lookback'
if needle not in source:
    raise SystemExit('Expected stride logic not found in manager.py')
print('stride_logic_found=yes')
