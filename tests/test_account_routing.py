"""Acceptance tests for dashboard-account-selector: per-market account routing.

Acceptance criteria (from CLAUDE_CODE_REWRITE_GUIDE.md):
  - 0005.HK uses HK account id.
  - TSLA uses US account id.
  - Missing HK account blocks HK order with a clear structured error.
  - Legacy futu.target_acc_id remains a fallback.
  - Dashboard/API snapshot exposes account mapping without secrets.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from v3_pipeline.config.manager import (
    ConfigManager,
    FutuCfg,
    FutuMarketAccountCfg,
)
from v3_pipeline.core.futu_connector import FutuConfig, FutuConnector


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_futu_env(monkeypatch):
    """Strip all FUTU_* env overrides so defaults are predictable."""
    for k in list(os.environ):
        if k.startswith("FUTU_"):
            monkeypatch.delenv(k, raising=False)
    yield


def _make_connector_with_accounts(hk_acc: int | None, us_acc: int | None, global_acc: int | None = None) -> FutuConnector:
    """Build a FutuConnector with pre-populated config_market_accounts (no real broker)."""
    cfg = FutuConfig(host="127.0.0.1", port=11111, trd_env="SIMULATE")
    cfg.target_acc_id = global_acc
    conn = FutuConnector.__new__(FutuConnector)
    conn.config = cfg
    conn.config_market_accounts = {}
    conn.account_ids = {}
    conn.logger = MagicMock()
    if hk_acc is not None:
        conn.config_market_accounts["HK"] = hk_acc
    if us_acc is not None:
        conn.config_market_accounts["US"] = us_acc
    return conn


# ── Config parsing ────────────────────────────────────────────────────────────


def test_config_manager_parses_per_market_accounts(tmp_path):
    cfg_data = {
        "futu": {
            "host": "127.0.0.1",
            "port": 11112,
            "trd_env": "SIMULATE",
            "target_acc_id": 99999,
            "accounts": {
                "HK": {"target_acc_id": 11111111},
                "US": {"target_acc_id": 22222222},
            },
        }
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg_data), encoding="utf-8")

    mgr = ConfigManager(str(p))
    assert mgr.futu.account_id_for_market("HK") == 11111111
    assert mgr.futu.account_id_for_market("US") == 22222222
    assert mgr.futu.target_acc_id == 99999  # legacy fallback unchanged


def test_config_manager_no_accounts_section(tmp_path):
    cfg_data = {"futu": {"target_acc_id": 99999}}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg_data), encoding="utf-8")

    mgr = ConfigManager(str(p))
    assert mgr.futu.account_id_for_market("HK") is None
    assert mgr.futu.account_id_for_market("US") is None
    assert mgr.futu.target_acc_id == 99999


def test_env_var_overrides_per_market_account(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTU_HK_ACC_ID", "55551111")
    monkeypatch.setenv("FUTU_US_ACC_ID", "55552222")
    cfg_data = {
        "futu": {
            "accounts": {
                "HK": {"target_acc_id": 11111111},
                "US": {"target_acc_id": 22222222},
            }
        }
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg_data), encoding="utf-8")

    mgr = ConfigManager(str(p))
    assert mgr.futu.account_id_for_market("HK") == 55551111
    assert mgr.futu.account_id_for_market("US") == 55552222


# ── Account routing in FutuConnector ─────────────────────────────────────────


def test_hk_symbol_uses_hk_account():
    conn = _make_connector_with_accounts(hk_acc=11111111, us_acc=22222222)
    kwargs = conn._account_kwargs_for_market("HK")
    assert kwargs == {"acc_id": 11111111}


def test_us_symbol_uses_us_account():
    conn = _make_connector_with_accounts(hk_acc=11111111, us_acc=22222222)
    kwargs = conn._account_kwargs_for_market("US")
    assert kwargs == {"acc_id": 22222222}


def test_legacy_global_fallback_when_no_per_market_config():
    """When no explicit per-market accounts exist, fall back to global target_acc_id."""
    conn = _make_connector_with_accounts(hk_acc=None, us_acc=None, global_acc=99999)
    assert conn._account_kwargs_for_market("HK") == {"acc_id": 99999}
    assert conn._account_kwargs_for_market("US") == {"acc_id": 99999}


def test_config_beats_discovered_account():
    conn = _make_connector_with_accounts(hk_acc=11111111, us_acc=22222222)
    # Simulate broker discovery returning a different ID
    conn.account_ids["HK"] = 88888888
    conn.account_ids["US"] = 77777777
    assert conn._account_kwargs_for_market("HK") == {"acc_id": 11111111}
    assert conn._account_kwargs_for_market("US") == {"acc_id": 22222222}


def test_discovered_fills_gap_when_config_absent():
    conn = _make_connector_with_accounts(hk_acc=None, us_acc=22222222, global_acc=99999)
    conn.account_ids["HK"] = 88888888
    # HK: no explicit config → discovered
    assert conn._account_kwargs_for_market("HK") == {"acc_id": 88888888}
    # US: explicit config wins
    assert conn._account_kwargs_for_market("US") == {"acc_id": 22222222}


# ── Order blocking for missing market account ─────────────────────────────────


def test_place_order_blocks_hk_when_hk_account_missing():
    """When explicit accounts are configured for US only, HK orders must be blocked."""
    conn = _make_connector_with_accounts(hk_acc=None, us_acc=22222222, global_acc=None)

    # Provide minimal stubs so place_order reaches the account-guard check
    mock_ft = MagicMock()
    mock_ft.RET_OK = 0
    conn.ft = mock_ft
    conn.trade_ctxs = {"HK": MagicMock(), "US": MagicMock()}
    conn._quote_cache = {}

    with pytest.raises(RuntimeError, match="account_route_error"):
        conn.place_order("0005.HK", qty=100, side="BUY", price=60.0)


def test_place_order_allows_hk_when_hk_account_configured():
    """0005.HK succeeds when HK account is explicitly configured."""
    conn = _make_connector_with_accounts(hk_acc=11111111, us_acc=22222222)

    mock_ft = MagicMock()
    mock_ft.RET_OK = 0
    mock_ctx = MagicMock()
    mock_ctx.place_order.return_value = (0, MagicMock())
    conn.ft = mock_ft
    conn.trade_ctxs = {"HK": mock_ctx, "US": MagicMock()}
    conn._quote_cache = {}

    with (
        patch.object(conn, "get_order_reference_price", return_value=60.0),
        patch.object(conn, "validate_trading_ready"),
        patch.object(conn, "_resolved_trd_env", return_value="SIMULATE"),
    ):
        conn.place_order("0005.HK", qty=100, side="BUY", price=60.0)

    mock_ctx.place_order.assert_called_once()
    call_kwargs = mock_ctx.place_order.call_args.kwargs
    assert call_kwargs.get("acc_id") == 11111111


def test_place_order_no_blocking_without_explicit_config():
    """Without any config_market_accounts, backward-compat: no blocking, use global fallback."""
    conn = _make_connector_with_accounts(hk_acc=None, us_acc=None, global_acc=99999)

    mock_ft = MagicMock()
    mock_ft.RET_OK = 0
    mock_ctx = MagicMock()
    mock_ctx.place_order.return_value = (0, MagicMock())
    conn.ft = mock_ft
    conn.trade_ctxs = {"HK": mock_ctx, "US": MagicMock()}
    conn._quote_cache = {}

    with (
        patch.object(conn, "get_order_reference_price", return_value=60.0),
        patch.object(conn, "validate_trading_ready"),
        patch.object(conn, "_resolved_trd_env", return_value="SIMULATE"),
    ):
        conn.place_order("0005.HK", qty=100, side="BUY", price=60.0)

    mock_ctx.place_order.assert_called_once()
    call_kwargs = mock_ctx.place_order.call_args.kwargs
    assert call_kwargs.get("acc_id") == 99999


# ── Account mapping snapshot ──────────────────────────────────────────────────


def test_account_mapping_snapshot_no_secrets():
    conn = _make_connector_with_accounts(hk_acc=11111111, us_acc=22222222, global_acc=99999)
    conn.account_ids["HK"] = 88888888  # also add discovered

    snap = conn.account_mapping_snapshot()

    # Must not expose trade_password or host
    assert "trade_password" not in str(snap)
    assert "host" not in snap

    # Must show routing per market
    assert "account_routing" in snap
    routing = snap["account_routing"]
    assert routing["HK"]["resolved_acc_id"] == 11111111
    assert routing["HK"]["source"] == "config"
    assert routing["US"]["resolved_acc_id"] == 22222222
    assert routing["US"]["source"] == "config"
    assert snap["global_fallback_acc_id"] == 99999
    assert snap["trd_env"] == "SIMULATE"


def test_account_mapping_snapshot_uses_discovered_when_no_config():
    conn = _make_connector_with_accounts(hk_acc=None, us_acc=None, global_acc=99999)
    conn.account_ids["HK"] = 88888888
    conn.account_ids["US"] = 77777777

    snap = conn.account_mapping_snapshot()
    routing = snap["account_routing"]
    assert routing["HK"]["resolved_acc_id"] == 88888888
    assert routing["HK"]["source"] == "discovered"
    assert routing["US"]["resolved_acc_id"] == 77777777


def test_account_mapping_snapshot_fallback_source():
    conn = _make_connector_with_accounts(hk_acc=None, us_acc=None, global_acc=99999)
    # No per-market config or discovery — resolved via global fallback
    # Note: snapshot only iterates known markets from config_market_accounts + account_ids
    # When both are empty, routing dict will also be empty but global_fallback_acc_id shows it
    snap = conn.account_mapping_snapshot()
    assert snap["global_fallback_acc_id"] == 99999


# ── FutuConnector._load_runtime_config per-market loading ─────────────────────


def test_load_runtime_config_loads_per_market_accounts(tmp_path):
    cfg_data = {
        "futu": {
            "target_acc_id": 99999,
            "accounts": {
                "HK": {"target_acc_id": 11111111},
                "US": {"target_acc_id": 22222222},
            },
        }
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg_data), encoding="utf-8")

    conn = FutuConnector.__new__(FutuConnector)
    conn.config = FutuConfig()
    conn.config_market_accounts = {}
    conn.account_ids = {}
    conn.logger = MagicMock()

    conn._load_runtime_config(str(p))

    assert conn.config_market_accounts.get("HK") == 11111111
    assert conn.config_market_accounts.get("US") == 22222222


def test_load_runtime_config_env_overrides_per_market(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTU_HK_ACC_ID", "55551111")
    cfg_data = {
        "futu": {
            "accounts": {
                "HK": {"target_acc_id": 11111111},
            }
        }
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg_data), encoding="utf-8")

    conn = FutuConnector.__new__(FutuConnector)
    conn.config = FutuConfig()
    conn.config_market_accounts = {}
    conn.account_ids = {}
    conn.logger = MagicMock()

    conn._load_runtime_config(str(p))

    # Env var must win over config.json value
    assert conn.config_market_accounts.get("HK") == 55551111


def test_hk_not_using_us_global_acc_id_when_accounts_configured(tmp_path):
    """Regression: config.json with futu.accounts must route HK to HK acc_id,
    not fall back to the global target_acc_id (which is the US account)."""
    cfg_data = {
        "futu": {
            "target_acc_id": 18526451,   # US account as global target
            "accounts": {
                "HK": {"target_acc_id": 14239754},
                "US": {"target_acc_id": 18526451},
            },
        }
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg_data), encoding="utf-8")

    conn = FutuConnector.__new__(FutuConnector)
    conn.config = FutuConfig()
    conn.config_market_accounts = {}
    conn.account_ids = {}
    conn.logger = MagicMock()

    conn._load_runtime_config(str(p))

    assert conn._account_kwargs_for_market("HK") == {"acc_id": 14239754}, \
        "HK must use 14239754, not the global US target_acc_id 18526451"
    assert conn._account_kwargs_for_market("US") == {"acc_id": 18526451}
