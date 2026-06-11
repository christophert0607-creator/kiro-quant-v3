import importlib.util
import sqlite3
from pathlib import Path

_META_PATH = Path(__file__).resolve().parents[1] / "self_learn" / "meta_labeler.py"
_spec = importlib.util.spec_from_file_location("kiro_meta_labeler_under_test", _META_PATH)
_meta = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
import sys
sys.modules[_spec.name] = _meta
_spec.loader.exec_module(_meta)
MetaDecision = _meta.MetaDecision
MetaLabelGate = _meta.MetaLabelGate


def _make_db(path, *, source="synthetic_seed", n=100, profitable=60, symbol="TSLA"):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE predictions (id TEXT PRIMARY KEY, symbol TEXT, predicted_price REAL, confidence REAL, created_at TEXT);
        CREATE TABLE signals (id TEXT PRIMARY KEY, prediction_id TEXT, action TEXT, entry_price REAL, size INTEGER, status TEXT, created_at TEXT);
        CREATE TABLE outcomes (signal_id TEXT PRIMARY KEY, exit_price REAL, pnl REAL, pnl_pct REAL, hold_minutes INTEGER, prediction_error REAL, source TEXT, broker_order_id TEXT, recorded_by TEXT, provenance_meta TEXT, closed_at TEXT);
        """
    )
    for i in range(n):
        pred_id = f"p{i}"
        sig_id = f"s{i}"
        pnl = 10.0 if i < profitable else -10.0
        broker_order_id = f"ord{i}" if source in {"paper_broker", "live_broker"} else None
        conn.execute("INSERT INTO predictions VALUES (?, ?, ?, ?, datetime('now'))", (pred_id, symbol, 105.0, 0.7))
        conn.execute("INSERT INTO signals VALUES (?, ?, 'BUY', 100.0, 10, 'CLOSED', datetime('now'))", (sig_id, pred_id))
        conn.execute(
            "INSERT INTO outcomes VALUES (?, 101.0, ?, ?, 30, 1.0, ?, ?, 'pytest', '{\"ok\":true}', datetime('now'))",
            (sig_id, pnl, pnl / 100.0, source, broker_order_id),
        )
    conn.commit()
    conn.close()


def test_synthetic_only_outcomes_return_no_data(tmp_path):
    db = tmp_path / "synthetic.db"
    _make_db(db, source="synthetic_seed", n=100, profitable=100)

    decision = MetaLabelGate(db_path=db, min_real_outcomes=100).should_take_trade(
        "TSLA", "BUY", entry_price=100.0, predicted_price=103.0, confidence=0.8
    )

    assert decision.decision == MetaDecision.NO_DATA
    assert decision.eligible_outcomes == 0
    assert decision.source_ok is False


def test_paper_broker_profitable_history_confirms(tmp_path):
    db = tmp_path / "paper.db"
    _make_db(db, source="paper_broker", n=100, profitable=70)

    decision = MetaLabelGate(db_path=db, min_real_outcomes=100).should_take_trade(
        "TSLA", "BUY", entry_price=100.0, predicted_price=103.0, confidence=0.8
    )

    assert decision.decision == MetaDecision.CONFIRM
    assert decision.eligible_outcomes == 100
    assert decision.source_ok is True


def test_paper_broker_poor_history_rejects(tmp_path):
    db = tmp_path / "paper_bad.db"
    _make_db(db, source="paper_broker", n=100, profitable=35)

    decision = MetaLabelGate(db_path=db, min_real_outcomes=100).should_take_trade(
        "TSLA", "BUY", entry_price=100.0, predicted_price=103.0, confidence=0.8
    )

    assert decision.decision == MetaDecision.REJECT
