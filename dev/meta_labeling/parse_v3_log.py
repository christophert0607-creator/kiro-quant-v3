#!/usr/bin/env python3
"""Parse v3_output.log to generate decision_trace_us_sim.jsonl.

This converts live trading log entries into structured JSON records
suitable for meta-labeling pipeline.

Log format: 2026-03-26 09:46:07 | LiveTradingLoop | INFO | EXEC 1024.HK BUY qty=24 fill=46.8034 reason=model_signal_conf=0.014
"""

import json
import re
from datetime import datetime
from pathlib import Path

LOG_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| \S+ \| \S+ \| "
    r"(EXEC|PAPER_ORDER) (\S+) (BUY|SELL) qty=(\d+) fill=([\d.]+) type=\S+"
    r"(?: reason=(\S+))?"
)

def parse_log(log_path: Path) -> list[dict]:
    records = []
    for line in log_path.read_text(errors="ignore").splitlines():
        m = LOG_PATTERN.search(line)
        if not m:
            continue
        ts_str, action_type, symbol, side, qty, price, reason = m.groups()
        # Parse timestamp
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue

        # Map to decision trace schema
        if action_type == "EXEC":
            record_action = f"{side}_PLACED"
        else:
            # PAPER_ORDER -> blocked (not actually executed)
            record_action = f"{side}_BLOCKED_SIM"

        # Parse confidence from reason
        confidence = None
        if reason:
            # reason format: model_signal_conf=0.014, swing_signal_conf=0.092, max_hold_30_bars
            conf_match = re.search(r"conf=([\d.]+)", reason)
            if conf_match:
                confidence = float(conf_match.group(1))

        record = {
            "ts_utc": ts.isoformat(timespec="seconds"),
            "action": record_action,
            "symbol": symbol,
            "side": side,
            "qty": int(qty),
            "px": float(price),
            "confidence": confidence,
            "reason": reason,
            # Add source for traceability
            "source": "v3_output.log",
            "log_action": action_type,
        }
        records.append(record)
    return records


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="kiro-quant/v3_output.log")
    ap.add_argument("--out", default="learning/us_sim/decision_trace_us_sim.jsonl")
    args = ap.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"Error: log not found: {log_path}")
        return 1

    records = parse_log(log_path)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Extracted {len(records)} decision events -> {out_path}")
    # Show distribution
    actions = {}
    for r in records:
        a = r["action"]
        actions[a] = actions.get(a, 0) + 1
    print(f"Actions: {actions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())