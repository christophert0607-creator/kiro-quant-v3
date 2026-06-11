#!/usr/bin/env python3
"""Read-only audit for TradeQuality / Meta-label gate shadow runtime events.

This script intentionally does not import trading/runtime modules and does not write to
DB, config, or model artifacts.  It summarizes structured ``logs/decisions.jsonl``
events so the meta-labeling dev loop can verify shadow gates are producing usable
telemetry before any future enforcement step.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = ROOT / "logs" / "decisions.jsonl"
GATE_EVENTS = {"trade_quality_gate", "meta_label_gate"}


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _market(symbol: Any) -> str:
    text = str(symbol or "").upper()
    if text.endswith(".HK") or text.startswith("HK."):
        return "HK"
    if text:
        return "US"
    return "UNKNOWN"


def _iter_events(path: Path, cutoff: datetime | None):
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line_no, line in enumerate(fh, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") not in GATE_EVENTS:
                continue
            ts = _parse_ts(event.get("ts") or event.get("timestamp") or event.get("created_at"))
            if cutoff is not None and ts is not None and ts < cutoff:
                continue
            yield line_no, ts, event


def audit(path: Path, days: float) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None
    by_gate: dict[str, Any] = defaultdict(
        lambda: {
            "events": 0,
            "shadow": Counter(),
            "markets": Counter(),
            "decisions": Counter(),
            "reasons": Counter(),
            "symbols": Counter(),
            "score_sum": 0.0,
            "score_count": 0,
            "source_ok": Counter(),
            "first_ts": None,
            "last_ts": None,
        }
    )
    malformed = 0

    if not path.exists():
        return {"ok": False, "reason": "log_missing", "log": str(path), "days": days, "gates": {}}

    for _line_no, ts, event in _iter_events(path, cutoff):
        gate = str(event.get("event"))
        bucket = by_gate[gate]
        bucket["events"] += 1
        bucket["shadow"][str(bool(event.get("shadow", False))).lower()] += 1
        bucket["markets"][_market(event.get("symbol"))] += 1
        bucket["decisions"][str(event.get("decision", "UNKNOWN"))] += 1
        if event.get("symbol"):
            bucket["symbols"][str(event.get("symbol"))] += 1
        if gate == "trade_quality_gate":
            try:
                bucket["score_sum"] += float(event.get("score"))
                bucket["score_count"] += 1
            except (TypeError, ValueError):
                malformed += 1
            for reason in event.get("reasons") or []:
                bucket["reasons"][str(reason)] += 1
        elif gate == "meta_label_gate":
            bucket["source_ok"][str(bool(event.get("source_ok", False))).lower()] += 1
            reason = event.get("reason")
            if reason:
                bucket["reasons"][str(reason)] += 1
        if ts is not None:
            iso = ts.isoformat()
            if bucket["first_ts"] is None or iso < bucket["first_ts"]:
                bucket["first_ts"] = iso
            if bucket["last_ts"] is None or iso > bucket["last_ts"]:
                bucket["last_ts"] = iso

    gates: dict[str, Any] = {}
    for gate, bucket in sorted(by_gate.items()):
        avg_score = None
        if bucket["score_count"]:
            avg_score = round(bucket["score_sum"] / bucket["score_count"], 6)
        gates[gate] = {
            "events": bucket["events"],
            "first_ts": bucket["first_ts"],
            "last_ts": bucket["last_ts"],
            "shadow_counts": dict(bucket["shadow"]),
            "markets": dict(bucket["markets"]),
            "decisions": dict(bucket["decisions"]),
            "top_reasons": dict(bucket["reasons"].most_common(10)),
            "top_symbols": dict(bucket["symbols"].most_common(10)),
            "avg_score": avg_score,
            "source_ok_counts": dict(bucket["source_ok"]),
        }

    return {
        "ok": True,
        "log": str(path),
        "days": days,
        "cutoff_utc": cutoff.isoformat() if cutoff else None,
        "malformed_score_count": malformed,
        "gates": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=1.0, help="lookback window; <=0 reads whole log")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args()
    print(json.dumps(audit(args.log, args.days), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
