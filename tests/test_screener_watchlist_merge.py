"""
Regression for V3FunnelScreener.run() — must preserve the tiered schema added
by PR #85 (`version`, `tiers.{tier1,tier2,tier3}`, `metadata`) when it
rewrites `dynamic_watchlist.json`, instead of clobbering it with a flat
`{date, picks, details, failed_symbols}` payload.

Production failure: tests/test_universe_expansion.py::test_dynamic_watchlist_format
fails every morning because the screener cron at ~08:00 HKT replaces the file
with the old schema, dropping `version`/`tiers`/`metadata`.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest


# tests/test_idle_collect.py installs a `SimpleNamespace(V3FunnelScreener=object)`
# stub into sys.modules via setdefault. Once that ran in the same process, any
# later `from v3_pipeline.features.screener import V3FunnelScreener` returns the
# bare `object` class and our `V3FunnelScreener(watchlist_path=...)` call dies
# with `TypeError: object() takes no arguments`. Force-reload the real module
# here so this file works regardless of test-collection order.
sys.modules.pop("v3_pipeline.features.screener", None)
_screener_mod = importlib.import_module("v3_pipeline.features.screener")
V3FunnelScreener = _screener_mod.V3FunnelScreener


def _seed_tiered_watchlist(path: Path, picks_in_tier1: list[str]) -> dict:
    payload = {
        "version": "2.0",
        "date": "2026-05-16 08:00:00",
        "expanded_universe": False,
        "tiers": {
            "tier1": {
                "description": "Active trading",
                "max_size": 20,
                "symbols": list(picks_in_tier1),
            },
            "tier2": {"description": "Full universe", "count": 300, "symbols": []},
            "tier3": {"description": "Watch only", "symbols": []},
        },
        "metadata": {"last_screened_at": "2026-05-16 08:00:00", "picks_count": len(picks_in_tier1)},
    }
    path.write_text(json.dumps(payload, indent=4))
    return payload


def _fake_screener_df(picks: list[str]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "symbol": s, "pe": 20.0, "eps_growth": 0.3, "rev_growth": 0.2,
            "peg": 1.0, "price": 100.0, "ma50": 95.0,
        }
        for s in picks
    ])


class TestScreenerPreservesTieredSchema:
    def test_run_keeps_version_tiers_metadata_when_file_exists(self, tmp_path):
        path = tmp_path / "dynamic_watchlist.json"
        _seed_tiered_watchlist(path, picks_in_tier1=["OLD1", "OLD2"])

        scr = V3FunnelScreener(watchlist_path=str(path))
        scr.watchlist_path = path  # override to tmp

        picks = ["NEW1", "NEW2", "NEW3"]
        with patch.object(scr, "fetch_data", return_value=_fake_screener_df(picks)):
            returned = scr.run(symbols=picks)

        assert returned == picks
        with open(path) as f:
            data = json.load(f)
        # Schema PR #85 expects
        assert "version" in data
        assert data["version"] == "2.0"
        assert "tiers" in data
        assert "tier1" in data["tiers"]
        assert "tier2" in data["tiers"]
        assert "tier3" in data["tiers"]
        assert "metadata" in data
        # Tier1 was updated with the new picks (replacing OLD1/OLD2)
        assert data["tiers"]["tier1"]["symbols"] == picks
        # Legacy fields still populated
        assert data["picks"] == picks

    def test_run_builds_schema_when_file_missing(self, tmp_path):
        path = tmp_path / "dynamic_watchlist.json"
        assert not path.exists()

        scr = V3FunnelScreener(watchlist_path=str(path))
        scr.watchlist_path = path

        picks = ["A", "B"]
        with patch.object(scr, "fetch_data", return_value=_fake_screener_df(picks)):
            scr.run(symbols=picks)

        with open(path) as f:
            data = json.load(f)
        assert "version" in data
        assert "tiers" in data
        assert "metadata" in data
        assert data["tiers"]["tier1"]["symbols"] == picks

    def test_run_caps_tier1_at_max_size(self, tmp_path):
        path = tmp_path / "dynamic_watchlist.json"
        _seed_tiered_watchlist(path, picks_in_tier1=[])
        # Override max_size
        with open(path) as f:
            d = json.load(f)
        d["tiers"]["tier1"]["max_size"] = 3
        path.write_text(json.dumps(d))

        scr = V3FunnelScreener(watchlist_path=str(path))
        scr.watchlist_path = path
        picks = [f"S{i}" for i in range(10)]
        with patch.object(scr, "fetch_data", return_value=_fake_screener_df(picks)):
            scr.run(symbols=picks)

        with open(path) as f:
            data = json.load(f)
        assert len(data["tiers"]["tier1"]["symbols"]) == 3
        assert data["tiers"]["tier1"]["symbols"] == picks[:3]
        # The full picks array still preserved at top level for legacy callers
        assert data["picks"] == picks

    def test_run_recovers_when_existing_file_corrupted(self, tmp_path):
        path = tmp_path / "dynamic_watchlist.json"
        path.write_text("not valid json {{{")

        scr = V3FunnelScreener(watchlist_path=str(path))
        scr.watchlist_path = path
        picks = ["A"]
        with patch.object(scr, "fetch_data", return_value=_fake_screener_df(picks)):
            scr.run(symbols=picks)

        with open(path) as f:
            data = json.load(f)
        # Even with a corrupted existing file, the schema is rebuilt cleanly
        assert "version" in data and "tiers" in data and "metadata" in data

    def test_metadata_records_screener_run(self, tmp_path):
        path = tmp_path / "dynamic_watchlist.json"
        _seed_tiered_watchlist(path, picks_in_tier1=[])

        scr = V3FunnelScreener(watchlist_path=str(path))
        scr.watchlist_path = path
        with patch.object(scr, "fetch_data", return_value=_fake_screener_df(["X", "Y"])):
            scr.run(symbols=["X", "Y"])

        with open(path) as f:
            data = json.load(f)
        meta = data["metadata"]
        assert meta["last_screener"] == "V3FunnelScreener"
        assert meta["picks_count"] == 2
        assert "last_screened_at" in meta
