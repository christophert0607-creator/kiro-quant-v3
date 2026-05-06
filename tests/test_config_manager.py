"""Tests for v3_pipeline.config.manager (Phase 2 - typed config loader)."""

import json
from pathlib import Path

import pytest

from v3_pipeline.config.manager import (
    AppConfig,
    CapitalBucketsCfg,
    ConfigManager,
    FutuCfg,
    ModelCfg,
    V3LiveCfg,
    load_config,
    _bool_from_str,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_config(data: dict, tmp_path: Path) -> str:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


MINIMAL_CONFIG = {
    "auto_trade": True,
    "paper_trading": False,
    "model": {
        "input_dim": 26,
        "markets": {"HK": "v3_hk_stocks", "US": "v3_us_stocks", "IDLE": "v3_us_stocks"},
    },
    "v3_live": {
        "symbols_list": ["0700.HK", "TSLA"],
        "polling_seconds": 60,
        "auto_trade": True,
        "paper_trading": False,
        "max_positions": 15,
    },
    "futu": {
        "host": "127.0.0.1",
        "port": 11112,
        "trd_env": "SIMULATE",
        "target_acc_id": 18526451,
    },
}


# ── Load defaults ─────────────────────────────────────────────────────────────

def test_loads_defaults_when_file_missing():
    mgr = ConfigManager("/tmp/__nonexistent_config_xyz__.json")
    cfg = mgr.app
    assert isinstance(cfg, AppConfig)
    assert isinstance(cfg.futu, FutuCfg)
    assert isinstance(cfg.v3_live, V3LiveCfg)
    assert isinstance(cfg.model, ModelCfg)
    assert cfg.futu.host == "127.0.0.1"
    assert cfg.futu.port == 11111
    assert cfg.v3_live.max_positions == 10


def test_loads_json_values(tmp_path):
    path = _write_config(MINIMAL_CONFIG, tmp_path)
    mgr = ConfigManager(path)

    assert mgr.futu.host == "127.0.0.1"
    assert mgr.futu.port == 11112
    assert mgr.futu.trd_env == "SIMULATE"
    assert mgr.futu.target_acc_id == 18526451
    assert mgr.live.max_positions == 15
    assert mgr.live.polling_seconds == 60
    assert "0700.HK" in mgr.live.symbols_list
    assert "TSLA" in mgr.live.symbols_list
    assert mgr.model.input_dim == 26


def test_app_top_level_flags(tmp_path):
    path = _write_config({"auto_trade": False, "paper_trading": True}, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.app.auto_trade is False
    assert mgr.app.paper_trading is True


# ── Section fallback ──────────────────────────────────────────────────────────

def test_futu_section_falls_back_to_futu_api_config(tmp_path):
    data = {"futu_api_config": {"host": "192.168.1.1", "port": 22222, "trd_env": "REAL"}}
    path = _write_config(data, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.futu.host == "192.168.1.1"
    assert mgr.futu.port == 22222
    assert mgr.futu.trd_env == "REAL"


def test_v3_live_auto_trade_inherits_top_level_when_absent(tmp_path):
    data = {"auto_trade": False, "paper_trading": True, "v3_live": {}}
    path = _write_config(data, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.live.auto_trade is False
    assert mgr.live.paper_trading is True


# ── Capital buckets ───────────────────────────────────────────────────────────

def test_capital_buckets_loaded(tmp_path):
    data = {
        "v3_live": {
            "capital_buckets": {
                "fractions": {"long": 0.6, "mid": 0.2, "short": 0.1, "reserve": 0.1, "avoid": 0.0},
                "thresholds": {"long": 0.003, "mid": 0.002, "short": 0.001, "avoid": 0.01},
                "by_symbol": {"TSLA": "long"},
            }
        }
    }
    path = _write_config(data, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.live.capital_buckets.fractions["long"] == 0.6
    assert mgr.live.capital_buckets.by_symbol == {"TSLA": "long"}


def test_capital_buckets_default_when_absent(tmp_path):
    path = _write_config({}, tmp_path)
    mgr = ConfigManager(path)
    assert "long" in mgr.live.capital_buckets.fractions
    assert "reserve" in mgr.live.capital_buckets.fractions


# ── Env variable overrides ────────────────────────────────────────────────────

def test_env_overrides_futu_host(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTU_OPEND_HOST", "10.0.0.5")
    path = _write_config({"futu": {"host": "127.0.0.1", "port": 11111}}, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.futu.host == "10.0.0.5"


def test_env_overrides_futu_port(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTU_OPEND_PORT", "22222")
    path = _write_config({"futu": {"host": "127.0.0.1", "port": 11111}}, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.futu.port == 22222


def test_env_overrides_trd_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTU_TRD_ENV", "REAL")
    path = _write_config({"futu": {"trd_env": "SIMULATE"}}, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.futu.trd_env == "REAL"


def test_env_overrides_max_positions(tmp_path, monkeypatch):
    monkeypatch.setenv("V3_MAX_POSITIONS", "5")
    path = _write_config({"v3_live": {"max_positions": 20}}, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.live.max_positions == 5


def test_env_override_auto_trade_false(tmp_path, monkeypatch):
    monkeypatch.setenv("V3_AUTO_TRADE", "0")
    path = _write_config({"auto_trade": True}, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.app.auto_trade is False


def test_env_override_auto_trade_true(tmp_path, monkeypatch):
    monkeypatch.setenv("V3_AUTO_TRADE", "1")
    path = _write_config({"auto_trade": False}, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.app.auto_trade is True


def test_invalid_env_override_falls_back_to_json(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTU_OPEND_PORT", "not_a_number")
    path = _write_config({"futu": {"port": 11112}}, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.futu.port == 11112


# ── Validation ────────────────────────────────────────────────────────────────

def test_validation_errors_logged_not_raised_by_default(tmp_path):
    data = {"futu": {"port": 99999999, "host": ""}, "v3_live": {"max_positions": 0}}
    path = _write_config(data, tmp_path)
    mgr = ConfigManager(path)  # should not raise
    errors = mgr.app.validate()
    assert any("futu.host" in e for e in errors)
    assert any("max_positions" in e for e in errors)


def test_validation_raises_in_strict_mode(tmp_path):
    data = {"futu": {"host": "", "port": 11111, "trd_env": "SIMULATE"}}
    path = _write_config(data, tmp_path)
    with pytest.raises(ValueError, match="futu.host"):
        ConfigManager(path, strict=True)


def test_valid_config_has_no_errors(tmp_path):
    path = _write_config(MINIMAL_CONFIG, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.app.validate() == []


def test_futu_invalid_trd_env(tmp_path):
    data = {"futu": {"trd_env": "PAPER"}}
    path = _write_config(data, tmp_path)
    mgr = ConfigManager(path)
    errors = mgr.app.futu.validate()
    assert any("trd_env" in e for e in errors)


# ── Reload ────────────────────────────────────────────────────────────────────

def test_reload_picks_up_changes(tmp_path):
    data = {"futu": {"port": 11111}}
    path = _write_config(data, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.futu.port == 11111

    Path(path).write_text(json.dumps({"futu": {"port": 22222}}), encoding="utf-8")
    mgr.reload()
    assert mgr.futu.port == 22222


# ── Module-level load_config ──────────────────────────────────────────────────

def test_load_config_returns_same_instance(tmp_path):
    import v3_pipeline.config.manager as mod
    mod._default_manager = None  # reset singleton for test isolation
    path = _write_config(MINIMAL_CONFIG, tmp_path)
    a = load_config(path)
    b = load_config(path)
    assert a is b
    mod._default_manager = None  # clean up


def test_load_config_creates_new_when_path_changes(tmp_path):
    import v3_pipeline.config.manager as mod
    mod._default_manager = None
    path1 = _write_config({"futu": {"port": 11111}}, tmp_path)
    path2 = tmp_path / "config2.json"
    path2.write_text(json.dumps({"futu": {"port": 22222}}), encoding="utf-8")

    a = load_config(str(path1))
    b = load_config(str(path2))
    assert a is not b
    assert a.futu.port == 11111
    assert b.futu.port == 22222
    mod._default_manager = None


# ── Raw access ────────────────────────────────────────────────────────────────

def test_raw_returns_copy(tmp_path):
    path = _write_config(MINIMAL_CONFIG, tmp_path)
    mgr = ConfigManager(path)
    raw = mgr.raw()
    raw["injected"] = True
    assert "injected" not in mgr.raw()


# ── _bool_from_str ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("val,expected", [
    ("1", True),
    ("true", True),
    ("yes", True),
    ("on", True),
    ("True", True),
    ("0", False),
    ("false", False),
    ("no", False),
    ("off", False),
    ("False", False),
    ("", False),
])
def test_bool_from_str(val, expected):
    assert _bool_from_str(val) == expected


# ── Model config ──────────────────────────────────────────────────────────────

def test_model_markets_from_config(tmp_path):
    data = {"model": {"input_dim": 32, "markets": {"HK": "custom_hk", "US": "custom_us"}}}
    path = _write_config(data, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.model.input_dim == 32
    assert mgr.model.markets["HK"] == "custom_hk"
    assert mgr.model.markets["US"] == "custom_us"


def test_model_defaults_when_section_absent(tmp_path):
    path = _write_config({}, tmp_path)
    mgr = ConfigManager(path)
    assert mgr.model.input_dim == 26
    assert "HK" in mgr.model.markets
