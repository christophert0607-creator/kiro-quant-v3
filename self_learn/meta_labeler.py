"""Meta-label gate for V3 pre-order filtering.

The gate only treats outcomes with broker/paper provenance as eligible. Seeded or
synthetic outcomes are deliberately ignored so live decisions do not learn from
fabricated performance.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class MetaDecision(str, Enum):
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"
    REVERSE = "REVERSE"
    NO_DATA = "NO_DATA"


@dataclass(frozen=True)
class MetaLabelResult:
    decision: MetaDecision
    eligible_outcomes: int
    win_rate: float | None
    source_ok: bool
    reason: str
    allow_reverse: bool = False


class MetaLabelGate:
    REAL_SOURCES = {"paper_broker", "live_broker"}

    def __init__(
        self,
        db_path: str | Path | None = None,
        min_real_outcomes: int = 100,
        confirm_win_rate: float = 0.55,
        reject_win_rate: float = 0.45,
        allow_reverse: bool = False,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path(__file__).parent / "trading_bot.db"
        self.min_real_outcomes = int(min_real_outcomes)
        self.confirm_win_rate = float(confirm_win_rate)
        self.reject_win_rate = float(reject_win_rate)
        self.allow_reverse = bool(allow_reverse)

    def should_take_trade(
        self,
        symbol: str,
        action: str,
        entry_price: float,
        predicted_price: float,
        confidence: float,
        indicators: Mapping[str, float] | None = None,
    ) -> MetaLabelResult:
        if not self.db_path.exists():
            return MetaLabelResult(MetaDecision.NO_DATA, 0, None, False, "db_missing")

        try:
            rows = self._eligible_rows(str(symbol), str(action).upper())
        except sqlite3.Error as exc:
            return MetaLabelResult(MetaDecision.NO_DATA, 0, None, False, f"schema_or_query_error:{exc}")

        eligible = len(rows)
        if eligible < self.min_real_outcomes:
            return MetaLabelResult(MetaDecision.NO_DATA, eligible, None, False, "insufficient_real_outcomes")

        wins = sum(1 for row in rows if float(row["pnl"] or 0.0) > 0)
        win_rate = wins / max(eligible, 1)
        if win_rate >= self.confirm_win_rate:
            return MetaLabelResult(MetaDecision.CONFIRM, eligible, win_rate, True, "real_source_win_rate_confirm")
        if win_rate <= self.reject_win_rate:
            return MetaLabelResult(MetaDecision.REJECT, eligible, win_rate, True, "real_source_win_rate_reject")
        return MetaLabelResult(MetaDecision.NO_DATA, eligible, win_rate, True, "win_rate_inconclusive")

    def _eligible_rows(self, symbol: str, action: str) -> list[sqlite3.Row]:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            outcome_cols = {r[1] for r in conn.execute("PRAGMA table_info(outcomes)").fetchall()}
            required = {"source", "broker_order_id", "provenance_meta", "pnl"}
            if not required.issubset(outcome_cols):
                return []
            rows = conn.execute(
                """
                SELECT o.pnl, o.source, o.broker_order_id, o.provenance_meta
                FROM outcomes o
                JOIN signals s ON s.id = o.signal_id
                JOIN predictions p ON p.id = s.prediction_id
                WHERE p.symbol = ?
                  AND UPPER(s.action) = ?
                  AND o.source IN ('paper_broker', 'live_broker')
                  AND (
                    (o.broker_order_id IS NOT NULL AND o.broker_order_id != '')
                    OR (o.provenance_meta IS NOT NULL AND o.provenance_meta != '')
                  )
                ORDER BY o.closed_at DESC
                """,
                (symbol, action),
            ).fetchall()
            return list(rows)
        finally:
            conn.close()
