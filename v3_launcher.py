#!/usr/bin/env python3
"""Launcher for Kiro V3 live paper auto-trading mode.

This launcher now supports a lightweight runtime profile so the system can run
with lower memory/CPU overhead when full model capacity is not required.
"""

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


# Load environment variables from .env file
def _load_env(env_path: str = ".env") -> None:
    """Load environment variables from .env file."""
    env_file = Path(env_path)
    if not env_file.exists():
        return
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value

# Load .env at startup
_load_env()


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
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON config: {config_path}") from exc
    return raw if isinstance(raw, dict) else {}


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _parse_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def build_live_config(config_path: str = "config.json") -> dict:
    cfg = _load_cfg(config_path)

    v3_live = cfg.get("v3_live", {}) if isinstance(cfg, dict) else {}

    root_auto_trade = _parse_bool(cfg.get("auto_trade"), True)
    root_paper_trading = _parse_bool(cfg.get("paper_trading"), True)

    return {
        "symbols_list": v3_live.get("symbols_list", ["TSLA", "TSLL", "NVDA"]),
        "polling_seconds": _parse_int(v3_live.get("polling_seconds", 15), 15),
        "prediction_threshold": 0.01,
        "prediction_thresholds": v3_live.get(
            "prediction_thresholds",
            {"TSLA": 0.01, "TSLL": 0.01, "NVDA": 0.01},
        ),
        "auto_trade": _parse_bool(v3_live.get("auto_trade"), root_auto_trade),
        "paper_trading": _parse_bool(v3_live.get("paper_trading"), root_paper_trading),
        "buy_cooldown_cycles": _parse_int(v3_live.get("buy_cooldown_cycles", 3), 3),
        "quick_take_profit_pct": float(v3_live.get("quick_take_profit_pct", 0.01)),
        "stop_loss_pct": float(v3_live.get("stop_loss_pct", 0.02)),
        "max_hold_bars": _parse_int(v3_live.get("max_hold_bars", 5), 5),
        "max_positions": _parse_int(v3_live.get("max_positions", 5), 5),
        "max_position_fraction_per_symbol": float(v3_live.get("max_position_fraction_per_symbol", 0.30)),
    }


def build_risk_config(config_path: str = "config.json") -> dict[str, float]:
    cfg = _load_cfg(config_path)
    v3_live = cfg.get("v3_live", {}) if isinstance(cfg, dict) else {}
    return {
        "ruin_threshold": float(v3_live.get("ruin_threshold", 0.15)),
        "max_position_fraction": min(1.0, max(0.0, float(v3_live.get("max_position_fraction_per_symbol", 0.30)))),
    }


def resolve_runtime_profile(config_path: str = "config.json", override: Optional[str] = None) -> RuntimeProfile:
    if override:
        key = override.strip().lower()
    else:
        cfg = _load_cfg(config_path)
        v3_live = cfg.get("v3_live", {}) if isinstance(cfg, dict) else {}
        key = str(v3_live.get("runtime_profile", "lite")).lower()

    profile = RUNTIME_PROFILES.get(key)
    if profile:
        return profile
    print(f"[launcher] unknown runtime profile '{key}', fallback to 'lite'")
    return RUNTIME_PROFILES["lite"]


def run_kiro_v35(config_path: str = "config.json", profile_override: Optional[str] = None, dry_run: bool = False) -> None:
    live_cfg_data = build_live_config(config_path)
    risk_cfg_data = build_risk_config(config_path)
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
    from v3_pipeline.risk.manager import RiskConfig, RiskController

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

    risk_cfg = RiskConfig(
        ruin_threshold=risk_cfg_data["ruin_threshold"],
        max_position_fraction=risk_cfg_data["max_position_fraction"],
    )

    loop = LiveTradingLoop(
        model_manager=manager,
        risk_controller=RiskController(config=risk_cfg),
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
