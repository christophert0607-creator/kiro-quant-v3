import json
from pathlib import Path

from preflight import run_preflight


def _write_config(path: Path, paper: bool = True):
    payload = {
        "auto_trade": True,
        "paper_trading": paper,
        "market": "HK",
        "data_source_preference": ["yfinance"],
        "futu_api_config": {"host": "127.0.0.1", "port": 65530, "cache_enabled": True, "cache_ttl_seconds": 60, "retry_count": 3, "retry_delay_seconds": 1, "log_level": "INFO"},
        "market_defaults": {"default_market": "HK", "default_kline_type": "day", "default_kline_count": 100, "default_orderbook_depth": 5},
        "v3_live": {
            "symbols_list": ["0700.HK"],
            "runtime_profile": "lite",
            "prediction_thresholds": {"0700.HK": 0.01},
            "buy_cooldown_cycles": 3,
            "polling_seconds": 60,
            "auto_trade": True,
            "paper_trading": paper,
            "stop_loss_pct": 0.03,
            "quick_take_profit_pct": 0.06,
            "max_hold_bars": 5,
            "max_positions": 10,
            "max_position_fraction_per_symbol": 0.3,
            "ruin_threshold": 0.15,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_preflight_warns_but_passes_for_paper_mode(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    _write_config(cfg, paper=True)
    monkeypatch.delenv("INFOWAY_API_KEY", raising=False)
    warnings, errors = run_preflight(str(cfg), strict=False)
    assert errors == []
    assert any("INFOWAY_API_KEY" in w for w in warnings)


def test_preflight_fails_for_live_mode_without_trade_password(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    _write_config(cfg, paper=False)
    monkeypatch.delenv("INFOWAY_API_KEY", raising=False)
    monkeypatch.delenv("FUTU_TRADE_PASSWORD", raising=False)
    monkeypatch.delenv("FUTU_TRADE_PWD", raising=False)
    warnings, errors = run_preflight(str(cfg), strict=True)
    assert errors
    assert any("FUTU_TRADE_PASSWORD" in e or "FUTU_TRADE_PWD" in e for e in errors)
