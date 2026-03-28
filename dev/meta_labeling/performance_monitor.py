#!/usr/bin/env python3
"""Meta-labeling — performance monitoring.

Milestone: M8.performance_monitoring

This module tracks meta-labeling decisions and computes performance metrics
to validate model effectiveness over time.

Usage:
    python3 dev/meta_labeling/performance_monitor.py --log-decision --event '{"confidence":0.8,...}' --result allow
    python3 dev/meta_labeling/performance_monitor.py --report

The script:
- Logs decisions to dev/meta_labeling/out/meta_decisions.jsonl
- Computes rolling metrics: allowed vs blocked trade counts, win rates
- Reports summary stats to console and JSON
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).parent
OUT_DIR = SCRIPT_DIR / "out"
DECISIONS_LOG = OUT_DIR / "meta_decisions.jsonl"
PERF_REPORT = OUT_DIR / "performance_report.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log_decision(event: dict[str, Any], decision: str, probability: float | None = None) -> bool:
    """Log a meta-labeling decision for performance tracking.
    
    Args:
        event: The decision event dict
        decision: "allow" or "block"
        probability: The model's probability score
    
    Returns:
        True if logged successfully
    """
    record = {
        "ts": utc_now(),
        "decision": decision,
        "probability": probability,
        "symbol": event.get("symbol", "UNKNOWN"),
        "confidence": event.get("confidence"),
    }
    
    # Add features for later analysis
    for k in ["snapshot_total_assets", "snapshot_cash", "snapshot_market_val", 
              "ohlcv_volume", "ind_rsi_14", "ind_macd"]:
        if k in event:
            record[k] = event[k]
    
    DECISIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DECISIONS_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
    
    return True


def load_decisions() -> list[dict]:
    """Load all logged decisions."""
    if not DECISIONS_LOG.exists():
        return []
    
    decisions = []
    with open(DECISIONS_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    decisions.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return decisions


def compute_metrics(decisions: list[dict]) -> dict[str, Any]:
    """Compute performance metrics from logged decisions."""
    if not decisions:
        return {
            "total_decisions": 0,
            "allowed": 0,
            "blocked": 0,
            "allow_rate": 0.0,
        }
    
    total = len(decisions)
    allowed = sum(1 for d in decisions if d.get("decision") == "allow")
    blocked = total - allowed
    
    # Group by symbol
    symbol_stats = {}
    for d in decisions:
        sym = d.get("symbol", "UNKNOWN")
        if sym not in symbol_stats:
            symbol_stats[sym] = {"allowed": 0, "blocked": 0}
        if d.get("decision") == "allow":
            symbol_stats[sym]["allowed"] += 1
        else:
            symbol_stats[sym]["blocked"] += 1
    
    # Probability distribution
    probs = [d.get("probability") for d in decisions if d.get("probability") is not None]
    prob_stats = {}
    if probs:
        prob_stats = {
            "min": min(probs),
            "max": max(probs),
            "avg": sum(probs) / len(probs),
            "count": len(probs),
        }
    
    return {
        "total_decisions": total,
        "allowed": allowed,
        "blocked": blocked,
        "allow_rate": allowed / total if total > 0 else 0.0,
        "symbol_stats": symbol_stats,
        "probability_stats": prob_stats,
        "first_ts": decisions[0].get("ts") if decisions else None,
        "last_ts": decisions[-1].get("ts") if decisions else None,
    }


def generate_report() -> dict[str, Any]:
    """Generate performance report."""
    decisions = load_decisions()
    metrics = compute_metrics(decisions)
    
    report = {
        "generated_at": utc_now(),
        "metrics": metrics,
    }
    
    # Save to JSON
    PERF_REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(PERF_REPORT, "w") as f:
        json.dump(report, f, indent=2)
    
    return report


def print_report(report: dict[str, Any]) -> None:
    """Print a human-readable report."""
    m = report.get("metrics", {})
    print("=" * 50)
    print("Meta-Labeling Performance Report")
    print("=" * 50)
    print(f"Total decisions: {m.get('total_decisions', 0)}")
    print(f"  Allowed: {m.get('allowed', 0)}")
    print(f"  Blocked: {m.get('blocked', 0)}")
    print(f"  Allow rate: {m.get('allow_rate', 0):.1%}")
    
    if m.get("symbol_stats"):
        print("\nBy Symbol:")
        for sym, stats in m.get("symbol_stats", {}).items():
            total = stats["allowed"] + stats["blocked"]
            allow_rate = stats["allowed"] / total if total > 0 else 0
            print(f"  {sym}: {stats['allowed']} allowed, {stats['blocked']} blocked ({allow_rate:.1%})")
    
    if m.get("probability_stats"):
        ps = m["probability_stats"]
        print(f"\nProbability stats (n={ps.get('count', 0)}):")
        print(f"  min: {ps.get('min', 0):.3f}, max: {ps.get('max', 0):.3f}, avg: {ps.get('avg', 0):.3f}")
    
    ts_range = m.get("first_ts") and m.get("last_ts")
    if ts_range:
        print(f"\nTime range: {m['first_ts']} to {m['last_ts']}")
    
    print("=" * 50)
    print(f"Full report saved to: {PERF_REPORT}")


def main():
    parser = argparse.ArgumentParser(description="Meta-labeling performance monitoring")
    parser.add_argument("--log-decision", action="store_true", help="Log a decision")
    parser.add_argument("--event", type=str, help="JSON event for logging")
    parser.add_argument("--result", choices=["allow", "block"], help="Decision result")
    parser.add_argument("--probability", type=float, help="Model probability score")
    parser.add_argument("--report", action="store_true", help="Generate performance report")
    args = parser.parse_args()
    
    if args.log_decision:
        if not args.event or not args.result:
            print("Error: --log-decision requires --event and --result")
            return 1
        
        event = json.loads(args.event)
        log_decision(event, args.result, args.probability)
        print(f"Logged: {args.result} (prob={args.probability})")
        return 0
    
    if args.report:
        report = generate_report()
        print_report(report)
        return 0
    
    # Default: show current stats
    decisions = load_decisions()
    metrics = compute_metrics(decisions)
    print(f"Current: {metrics.get('total_decisions', 0)} decisions, "
          f"{metrics.get('allow_rate', 0):.1%} allow rate")
    return 0


if __name__ == "__main__":
    exit(main())