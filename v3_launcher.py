#!/usr/bin/env python3
"""Launcher for Kiro V3 live paper auto-trading mode."""

import json
from pathlib import Path

from v3_pipeline.core.futu_connector import FutuConnector
from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop
from v3_pipeline.models.brain import KiroLSTM
from v3_pipeline.models.manager import DataPreparer, ModelManager
from v3_pipeline.risk.manager import RiskController


def build_live_config(config_path: str = "config.json") -> LiveConfig:
    cfg = {}
    p = Path(config_path)
    if p.exists():
        cfg = json.loads(p.read_text(encoding="utf-8"))

    v3_live = cfg.get("v3_live", {}) if isinstance(cfg, dict) else {}

    # --- Configuration section (requested) ---
    return LiveConfig(
        symbols_list=v3_live.get("symbols_list", ["TSLA", "TSLL", "NVDA"]),
        polling_seconds=int(v3_live.get("polling_seconds", 15)),
        prediction_threshold=0.01,
        prediction_thresholds=v3_live.get(
            "prediction_thresholds",
            {"TSLA": 0.01, "TSLL": 0.01, "NVDA": 0.01},
        ),
        auto_trade=bool(v3_live.get("auto_trade", cfg.get("auto_trade", True))),
        paper_trading=bool(v3_live.get("paper_trading", cfg.get("paper_trading", True))),
        buy_cooldown_cycles=int(v3_live.get("buy_cooldown_cycles", 3)),
    )


def run_kiro_v35() -> None:
    live_cfg = build_live_config()

    preparer = DataPreparer(lookback=60, target_col="Close")
    model = KiroLSTM(input_dim=26, hidden_dim=64, num_layers=2, dropout=0.2, output_dim=1)
    manager = ModelManager(model=model, data_preparer=preparer)

    loop = LiveTradingLoop(
        model_manager=manager,
        risk_controller=RiskController(),
        futu_connector=FutuConnector(),
        config=live_cfg,
    )
    loop.start()


if __name__ == "__main__":
    run_kiro_v35()
