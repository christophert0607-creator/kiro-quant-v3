#!/usr/bin/env python3
"""Launcher for Kiro V3 live paper auto-trading mode.

This launcher now supports a lightweight runtime profile so the system can run
with lower memory/CPU overhead when full model capacity is not required.
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional



@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    lookback: int
    input_dim: int
    hidden_dim: int
    num_layers: int
    dropout: float


RUNTIME_PROFILES = {
    "standard": RuntimeProfile(
        name="standard",
        lookback=60,
        input_dim=26,
        hidden_dim=64,
        num_layers=2,
        dropout=0.2,
    ),
    "lite": RuntimeProfile(
        name="lite",
        lookback=40,
        input_dim=26,
        hidden_dim=32,
        num_layers=1,
        dropout=0.1,
    ),
}


def _load_cfg(config_path: str) -> dict:
    p = Path(config_path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def build_live_config(config_path: str = "config.json") -> dict:
    cfg = _load_cfg(config_path)

    v3_live = cfg.get("v3_live", {}) if isinstance(cfg, dict) else {}

    return {
        "symbols_list": v3_live.get("symbols_list", ["TSLA", "TSLL", "NVDA"]),
        "polling_seconds": int(v3_live.get("polling_seconds", 15)),
        "prediction_threshold": 0.01,
        "prediction_thresholds": v3_live.get(
            "prediction_thresholds",
            {"TSLA": 0.01, "TSLL": 0.01, "NVDA": 0.01},
        ),
        "auto_trade": bool(v3_live.get("auto_trade", cfg.get("auto_trade", True))),
        "paper_trading": bool(v3_live.get("paper_trading", cfg.get("paper_trading", True))),
        "buy_cooldown_cycles": int(v3_live.get("buy_cooldown_cycles", 3)),
    }


def resolve_runtime_profile(config_path: str = "config.json", override: Optional[str] = None) -> RuntimeProfile:
    if override:
        key = override.strip().lower()
    else:
        cfg = _load_cfg(config_path)
        v3_live = cfg.get("v3_live", {}) if isinstance(cfg, dict) else {}
        key = str(v3_live.get("runtime_profile", "lite")).lower()

    return RUNTIME_PROFILES.get(key, RUNTIME_PROFILES["lite"])


def run_kiro_v35(config_path: str = "config.json", profile_override: Optional[str] = None, dry_run: bool = False) -> None:
    live_cfg_data = build_live_config(config_path)
    profile = resolve_runtime_profile(config_path=config_path, override=profile_override)

    if dry_run:
        print(
            f"[dry-run] profile={profile.name} lookback={profile.lookback} "
            f"hidden_dim={profile.hidden_dim} layers={profile.num_layers}"
        )
        print(f"[dry-run] symbols={','.join(live_cfg_data['symbols_list'])} auto_trade={live_cfg_data['auto_trade']}")
        return

    from v3_pipeline.core.futu_connector import FutuConnector
    from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop
    from v3_pipeline.models.brain import KiroLSTM
    from v3_pipeline.models.manager import DataPreparer, ModelManager
    from v3_pipeline.risk.manager import RiskController

    preparer = DataPreparer(lookback=profile.lookback, target_col="Close")
    model = KiroLSTM(
        input_dim=profile.input_dim,
        hidden_dim=profile.hidden_dim,
        num_layers=profile.num_layers,
        dropout=profile.dropout,
        output_dim=1,
    )
    manager = ModelManager(model=model, data_preparer=preparer)

    live_cfg = LiveConfig(**live_cfg_data)

    loop = LiveTradingLoop(
        model_manager=manager,
        risk_controller=RiskController(),
        futu_connector=FutuConnector(),
        config=live_cfg,
    )
    loop.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Kiro V3 launcher")
    parser.add_argument("--config", default="config.json", help="Path to config json")
    parser.add_argument("--profile", choices=sorted(RUNTIME_PROFILES.keys()), help="Runtime profile override")
    parser.add_argument("--dry-run", action="store_true", help="Print resolved runtime and exit")
    args = parser.parse_args()
    run_kiro_v35(config_path=args.config, profile_override=args.profile, dry_run=args.dry_run)
