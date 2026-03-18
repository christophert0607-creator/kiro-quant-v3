from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_all(base_dir: Path) -> Dict[str, Any]:
    config_dir = base_dir / "config"
    return {
        "markets": load_yaml(config_dir / "markets.yaml"),
        "indicators": load_yaml(config_dir / "indicators.yaml"),
        "backtest": load_yaml(config_dir / "backtest.yaml"),
        "risk": load_yaml(config_dir / "risk.yaml"),
    }


def get_market(markets_cfg: Dict[str, Any], code: str) -> Dict[str, Any]:
    for market in markets_cfg.get("markets", []):
        if market.get("code") == code:
            return market
    raise KeyError(f"Unknown market code: {code}")