#!/usr/bin/env python3
"""Generate synthetic decision trace + snapshot data for testing the meta-labeling pipeline.

Since real data windows don't overlap (decisions from 09:xx UTC, snapshots from 19:xx UTC),
this creates synthetic data that follows the same schema for pipeline testing.

This is for development/testing only - not used in production.
"""

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SYMBOLS = ["NVDA", "AAPL", "MSFT", "GOOGL", "AMZN"]
ACTIONS = ["BUY_PLACED", "BUY_BLOCKED_SIM", "SELL_PLACED", "SELL_BLOCKED_SIM"]

def generate_decisions(n: int = 100) -> list[dict]:
    """Generate synthetic decision trace events."""
    # Use a past date with yfinance data (2026-03-20)
    # US market hours: 9:30-16:00 ET = 14:30-21:00 UTC
    base = datetime(2026, 3, 20, 14, 30, tzinfo=timezone.utc)
    records = []
    for i in range(n):
        ts = base + timedelta(minutes=random.randint(0, 600))
        action = random.choice(ACTIONS)
        symbol = random.choice(SYMBOLS)
        price = round(random.uniform(30, 500), 4)
        conf = round(random.uniform(0.0, 0.2), 3) if "PLACED" in action else None
        
        records.append({
            "ts_utc": ts.isoformat(timespec="seconds"),
            "action": action,
            "symbol": symbol,
            "side": action.split("_")[0],
            "qty": random.randint(10, 100),
            "px": price,
            "confidence": conf,
            "reason": f"test_signal_conf={conf}" if conf else "test_signal",
            "source": "synthetic",
            "log_action": "EXEC" if "PLACED" in action else "PAPER_ORDER",
        })
    return records

def generate_snapshots(n: int = 50) -> list[dict]:
    """Generate synthetic account snapshots."""
    # Match decision dates (2026-03-20)
    base = datetime(2026, 3, 20, 8, 0, tzinfo=timezone.utc)
    records = []
    for i in range(n):
        ts = base + timedelta(minutes=random.randint(0, 720))
        total_assets = round(random.uniform(900000, 1100000), 3)
        cash = round(random.uniform(800000, 1000000), 3)
        market_val = total_assets - cash
        
        records.append({
            "schema": "kiro.us_sim.account_snap.v1",
            "ts_utc": ts.isoformat(timespec="seconds"),
            "ts_hkt": (ts + timedelta(hours=8)).isoformat(timespec="seconds"),
            "host": "127.0.0.1",
            "port": 11112,
            "acc_id": 18526451,
            "trd_env": "SIMULATE",
            "currency": "N/A",
            "total_assets": total_assets,
            "cash": cash,
            "market_val": market_val,
            "power": 0.0,
            "positions": [],
            "open_orders": [],
        })
    return records

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--decisions", type=int, default=100, help="num decision events")
    ap.add_argument("--snapshots", type=int, default=50, help="num account snapshots")
    ap.add_argument("--out-dec", default="learning/us_sim/decision_trace_us_sim.jsonl")
    ap.add_argument("--out-snap", default="learning/us_sim/account_snap_us_sim.jsonl")
    args = ap.parse_args()

    # Generate
    decisions = generate_decisions(args.decisions)
    snapshots = generate_snapshots(args.snapshots)

    # Write
    out_dec = Path(args.out_dec)
    out_dec.parent.mkdir(parents=True, exist_ok=True)
    with open(out_dec, "w") as f:
        for r in decisions:
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {len(decisions)} decisions -> {out_dec}")

    out_snap = Path(args.out_snap)
    with open(out_snap, "w") as f:
        for r in snapshots:
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {len(snapshots)} snapshots -> {out_snap}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())