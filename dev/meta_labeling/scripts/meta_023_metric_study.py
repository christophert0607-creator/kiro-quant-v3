#!/usr/bin/env python3
"""meta_023 — Alternative metric study for meta-labeling Phase 3 results.

Read-only diagnostic script. It opens the self_learn SQLite DB in read-only mode,
replays closed outcomes with a leave-one-out per (symbol, action) directional
accuracy estimate, and reports metrics that are more informative than a single
raw decision-accuracy number:

- coverage: fraction of outcomes with enough history to make a CONFIRM/REVERSE
  decision (NO_DATA is excluded from decision accuracy but still counted in P&L).
- covered_accuracy: accuracy over actionable decisions only.
- weighted_accuracy: covered accuracy weighted by abs(base pnl_pct), so mistakes
  on large movers matter more than tiny trades.
- pnl_delta_pct_points: meta-policy P&L minus base P&L in percentage points.

No live trading, risk, order routing, model, DB, or config state is modified.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

WORKSPACE = Path(__file__).resolve().parents[3]
DEFAULT_DB = WORKSPACE / "self_learn" / "trading_bot.db"


@dataclass(frozen=True)
class OutcomeRow:
    signal_id: str
    symbol: str
    action: str
    entry_price: float
    predicted_price: float
    exit_price: float
    pnl_pct: float
    source: str | None = None


@dataclass(frozen=True)
class ScoredOutcome:
    row: OutcomeRow
    decision: str
    history_count: int
    dir_acc: float | None
    correct: bool | None
    base_pnl_pct: float
    meta_pnl_pct: float


def _prediction_direction(row: OutcomeRow) -> int:
    if row.predicted_price > row.entry_price:
        return 1
    if row.predicted_price < row.entry_price:
        return -1
    return 0


def _actual_direction(row: OutcomeRow) -> int:
    if row.exit_price > row.entry_price:
        return 1
    if row.exit_price < row.entry_price:
        return -1
    return 0


def directional_correct(row: OutcomeRow) -> bool:
    return _prediction_direction(row) == _actual_direction(row)


def decision_from_dir_acc(dir_acc: float | None, confirm_threshold: float, reverse_threshold: float) -> str:
    if dir_acc is None:
        return "NO_DATA"
    if dir_acc >= confirm_threshold:
        return "CONFIRM"
    if dir_acc <= reverse_threshold:
        return "REVERSE"
    return "NO_DATA"


def score_decision(decision: str, base_pnl_pct: float) -> tuple[bool | None, float]:
    """Return (correct_for_accuracy, meta_pnl_pct). NO_DATA is not judged."""
    if decision == "CONFIRM":
        return base_pnl_pct > 0, base_pnl_pct
    if decision == "REVERSE":
        return base_pnl_pct < 0, -base_pnl_pct
    if decision == "REJECT":
        return base_pnl_pct <= 0, 0.0
    return None, base_pnl_pct


def score_outcomes(
    rows: Iterable[OutcomeRow],
    *,
    min_history: int = 1,
    confirm_threshold: float = 0.55,
    reverse_threshold: float = 0.40,
) -> list[ScoredOutcome]:
    rows = list(rows)
    grouped: dict[tuple[str, str], list[OutcomeRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.symbol, row.action.upper())].append(row)

    scored: list[ScoredOutcome] = []
    for row in rows:
        peers = [p for p in grouped[(row.symbol, row.action.upper())] if p.signal_id != row.signal_id]
        if len(peers) < min_history:
            dir_acc = None
        else:
            dir_acc = sum(1 for p in peers if directional_correct(p)) / len(peers)
        decision = decision_from_dir_acc(dir_acc, confirm_threshold, reverse_threshold)
        correct, meta_pnl = score_decision(decision, row.pnl_pct)
        scored.append(
            ScoredOutcome(
                row=row,
                decision=decision,
                history_count=len(peers),
                dir_acc=dir_acc,
                correct=correct,
                base_pnl_pct=row.pnl_pct,
                meta_pnl_pct=meta_pnl,
            )
        )
    return scored


def summarize(scored: Iterable[ScoredOutcome]) -> dict:
    scored = list(scored)
    judged = [s for s in scored if s.correct is not None]
    correct = [s for s in judged if s.correct]
    total_abs = sum(abs(s.base_pnl_pct) for s in judged)
    weighted_correct = sum(abs(s.base_pnl_pct) for s in correct)

    by_decision: dict[str, dict] = {}
    for decision in ("CONFIRM", "REVERSE", "REJECT", "NO_DATA"):
        items = [s for s in scored if s.decision == decision]
        judged_items = [s for s in items if s.correct is not None]
        by_decision[decision] = {
            "count": len(items),
            "judged": len(judged_items),
            "correct": sum(1 for s in judged_items if s.correct),
            "avg_base_pnl_pct": round(sum(s.base_pnl_pct for s in items) / len(items), 6) if items else None,
            "avg_meta_pnl_pct": round(sum(s.meta_pnl_pct for s in items) / len(items), 6) if items else None,
        }

    base_total = sum(s.base_pnl_pct for s in scored)
    meta_total = sum(s.meta_pnl_pct for s in scored)
    return {
        "total_outcomes": len(scored),
        "judged_outcomes": len(judged),
        "no_data_outcomes": sum(1 for s in scored if s.decision == "NO_DATA"),
        "coverage": round(len(judged) / len(scored), 6) if scored else 0.0,
        "covered_accuracy": round(len(correct) / len(judged), 6) if judged else None,
        "weighted_accuracy_by_abs_pnl": round(weighted_correct / total_abs, 6) if total_abs else None,
        "pnl_no_meta_pct_points": round(base_total, 6),
        "pnl_with_meta_pct_points": round(meta_total, 6),
        "pnl_delta_pct_points": round(meta_total - base_total, 6),
        "by_decision": by_decision,
    }


def load_rows(db_path: Path) -> list[OutcomeRow]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
              s.id AS signal_id,
              p.symbol AS symbol,
              UPPER(s.action) AS action,
              s.entry_price AS entry_price,
              p.predicted_price AS predicted_price,
              o.exit_price AS exit_price,
              COALESCE(o.pnl_pct, o.pnl, 0.0) AS pnl_pct,
              o.source AS source
            FROM outcomes o
            JOIN signals s ON s.id = o.signal_id
            JOIN predictions p ON p.id = s.prediction_id
            WHERE s.entry_price IS NOT NULL
              AND p.predicted_price IS NOT NULL
              AND o.exit_price IS NOT NULL
            ORDER BY o.closed_at ASC, s.id ASC
            """
        ).fetchall()
    finally:
        conn.close()

    return [
        OutcomeRow(
            signal_id=str(r["signal_id"]),
            symbol=str(r["symbol"]),
            action=str(r["action"]),
            entry_price=float(r["entry_price"]),
            predicted_price=float(r["predicted_price"]),
            exit_price=float(r["exit_price"]),
            pnl_pct=float(r["pnl_pct"]),
            source=r["source"],
        )
        for r in rows
    ]


def build_report(args: argparse.Namespace) -> dict:
    rows = load_rows(Path(args.db))
    scored = score_outcomes(
        rows,
        min_history=args.min_history,
        confirm_threshold=args.confirm_threshold,
        reverse_threshold=args.reverse_threshold,
    )
    report = summarize(scored)
    report.update(
        {
            "task": "meta_023",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "db_path": str(Path(args.db)),
            "metric_note": "coverage + covered_accuracy + abs-PnL weighted accuracy are safer Phase 4 readiness metrics than raw accuracy alone; NO_DATA is tracked separately.",
            "parameters": {
                "min_history": args.min_history,
                "confirm_threshold": args.confirm_threshold,
                "reverse_threshold": args.reverse_threshold,
                "leave_one_out": True,
            },
            "live_trading_changes": False,
        }
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="self_learn SQLite DB path (opened read-only)")
    parser.add_argument("--min-history", type=int, default=1, help="minimum peer outcomes per symbol/action before judging")
    parser.add_argument("--confirm-threshold", type=float, default=0.55)
    parser.add_argument("--reverse-threshold", type=float, default=0.40)
    return parser.parse_args()


def main() -> None:
    report = build_report(parse_args())
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
