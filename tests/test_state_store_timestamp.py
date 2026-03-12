import json
import time
from pathlib import Path

import state_store as ss


def test_load_sanitizes_future_order_timestamp(tmp_path: Path, monkeypatch):
    state_file = tmp_path / "state.json"
    future_ts = time.time() + 3600
    payload = {
        "date": str(__import__('datetime').date.today()),
        "daily_pnl": 0.0,
        "daily_trades": 0,
        "consecutive_errors": 0,
        "circuit_broken": False,
        "circuit_break_time": None,
        "last_orders": {
            "US.TSLA": {
                "hash": "abc",
                "time": future_ts,
                "signal": "BUY",
                "qty": 1,
                "price": 100.0,
            }
        },
        "positions": {},
        "total_invested": 0.0,
    }
    state_file.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(ss, "STATE_PATH", str(state_file))

    loaded = ss.load()
    assert loaded["last_orders"]["US.TSLA"]["time"] <= time.time() + 5
