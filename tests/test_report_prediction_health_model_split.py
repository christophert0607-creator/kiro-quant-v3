"""Tests for the --model V2/LSTM comparison in report_prediction_health.py."""

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "report_prediction_health", ROOT / "scripts" / "report_prediction_health.py"
)
rph = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rph)


def _ts(base: datetime, minutes: float) -> datetime:
    return base + timedelta(minutes=minutes)


def _events():
    """One V2 call that is directionally right, one that is wrong."""
    base = datetime(2026, 6, 11, 2, 0, tzinfo=timezone.utc)
    lstm = []
    # Price path for 0700.HK: 100 at t0, 102 at t+31 (up).
    for minute, price in ((0, 100.0), (31, 102.0), (62, 101.0)):
        lstm.append({"ts": _ts(base, minute), "symbol": "0700.HK", "pred": price * 0.999, "price": price})
    v2 = [
        # Predicts up at t0 (pred 101 > price 100), realized up at +31 → correct.
        {"ts": _ts(base, 0), "symbol": "0700.HK", "pred": 101.0, "price": 100.0,
         "prob_up": 0.65, "lstm_pred": 99.9},
        # Predicts up at t+31 (pred 103 > 102), realized down at +62 → wrong.
        {"ts": _ts(base, 31), "symbol": "0700.HK", "pred": 103.0, "price": 102.0,
         "prob_up": 0.45, "lstm_pred": 101.9},
    ]
    return v2, lstm


def test_v2_directional_accuracy_and_agreement():
    v2, lstm = _events()
    out = rph.build_model_comparison(v2, lstm, "both")
    assert out["v2"]["events"] == 2
    assert out["v2"]["evaluated"] == 2
    assert out["v2"]["directional_accuracy"] == 0.5
    # Both V2 events predict up while LSTM predicts down → zero agreement.
    assert out["v2"]["lstm_direction_agreement"] == 0.0
    assert out["v2"]["avg_prob_up"] == 0.55
    assert "lstm" in out


def test_calibration_buckets_track_realized_win_rate():
    v2, lstm = _events()
    out = rph.build_model_comparison(v2, lstm, "v2")
    buckets = out["v2"]["calibration_buckets"]
    # prob 0.65 event realized a win.
    assert buckets["0.6-1.0"] == {"n": 1, "realized_win_rate": 1.0}
    # prob 0.45 event realized a loss.
    assert buckets["0.4-0.5"] == {"n": 1, "realized_win_rate": 0.0}


def test_future_price_tolerance_window():
    base = datetime(2026, 6, 11, 2, 0, tzinfo=timezone.utc)
    prices = [(_ts(base, 31), 102.0)]
    assert rph._future_price(prices, base) == 102.0
    # Outside horizon+tolerance → None.
    assert rph._future_price(prices, _ts(base, -30)) is None


def test_cli_with_model_flag(tmp_path):
    base = datetime(2026, 6, 11, 2, 0, tzinfo=timezone.utc)
    log = tmp_path / "decisions.jsonl"
    lines = [
        {"ts": _ts(base, 0).isoformat(), "event": "model_predict",
         "symbol": "0700.HK", "pred": 99.9, "price": 100.0, "confidence": 0.2},
        {"ts": _ts(base, 0).isoformat(), "event": "hk_model_v2",
         "symbol": "0700.HK", "mode": "shadow", "pred": 101.0, "price": 100.0,
         "prob_up": 0.65, "lstm_pred": 99.9, "model_id": "hkalpha1_x"},
        {"ts": _ts(base, 31).isoformat(), "event": "model_predict",
         "symbol": "0700.HK", "pred": 102.1, "price": 102.0, "confidence": 0.2},
    ]
    log.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "report_prediction_health.py"),
         "--days", "99999", "--log", str(log), "--model", "both"],
        capture_output=True, text=True, check=True,
    )
    out = json.loads(proc.stdout)
    assert "model_comparison" in out
    assert out["model_comparison"]["v2"]["events"] == 1
    assert out["model_comparison"]["v2"]["directional_accuracy"] == 1.0
    assert out["markets"]["HK"]["predictions"] == 2


def test_cli_without_model_flag_keeps_legacy_shape(tmp_path):
    log = tmp_path / "decisions.jsonl"
    log.write_text("", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "report_prediction_health.py"),
         "--days", "1", "--log", str(log)],
        capture_output=True, text=True, check=True,
    )
    out = json.loads(proc.stdout)
    assert "model_comparison" not in out
