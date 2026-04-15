#!/usr/bin/env python3
"""Run KiroQuant V3 in safe test mode (no OpenD, no auto-trade).

Forces:
- NO_FUTU=1 (avoid OpenD ECONNREFUSED loops)
- IDLE_PRECOMPUTE=0 (avoid heavy precompute cycles)
- resolve_market_mode() => IDLE (avoid US_SYMBOLS huge universe during US hours)

Requires config.json to have auto_trade=false.
"""

from __future__ import annotations

import os


def main() -> None:
    os.environ.setdefault("NO_FUTU", "1")
    os.environ.setdefault("IDLE_PRECOMPUTE", "0")
    os.environ.setdefault("KIRO_MODE", "V3_TEST")

    import v3_launcher  # type: ignore

    # Force IDLE mode always (small universe = HK_SYMBOLS + TOP_PRECOMPUTE_US)
    v3_launcher.resolve_market_mode = lambda now=None: "IDLE"  # type: ignore

    v3_launcher.run_kiro_v35()


if __name__ == "__main__":
    main()
