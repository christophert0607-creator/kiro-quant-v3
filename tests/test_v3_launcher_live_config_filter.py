import json
from pathlib import Path

import v3_launcher as launcher


def test_build_live_config_excludes_risk_only_fields(tmp_path: Path) -> None:
    cfg = {
        "v3_live": {
            "symbols_list": ["NVDA"],
            "max_positions": 3,
            "max_position_fraction_per_symbol": 0.2,
            "ruin_threshold": 0.12,
        }
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")

    live_cfg = launcher.build_live_config(str(p))

    assert live_cfg["symbols_list"] == ["NVDA"]
    assert live_cfg["max_positions"] == 3
    assert "ruin_threshold" not in live_cfg


def test_build_risk_config_reads_ruin_threshold(tmp_path: Path) -> None:
    cfg = {
        "v3_live": {
            "max_position_fraction_per_symbol": 1.5,
            "ruin_threshold": 0.12,
        }
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")

    risk_cfg = launcher.build_risk_config(str(p))

    assert risk_cfg["ruin_threshold"] == 0.12
    assert risk_cfg["max_position_fraction"] == 1.0
