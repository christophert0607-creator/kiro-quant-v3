from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("GPUMonitor")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_path = Path("v3_pipeline/logs/gpu_usage.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    sh = logging.StreamHandler(sys.stderr)
    fmt = logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
    return logger


@dataclass
class GPUMonitorConfig:
    target_low: float = 25.0
    target_high: float = 35.0
    min_batch: int = 64
    max_batch: int = 512
    step: int = 32


class GPUMonitor:
    def __init__(self, config: GPUMonitorConfig | None = None) -> None:
        self.config = config or GPUMonitorConfig()
        self.logger = _build_logger()

    def get_gpu_utilization(self) -> float:
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                text=True,
                timeout=2,
            )
            vals = [float(x.strip()) for x in out.splitlines() if x.strip()]
            return float(sum(vals) / max(1, len(vals)))
        except Exception as exc:
            self.logger.warning("GPU utilization unavailable: %s", exc)
            return -1.0

    def adjust_batch_size(self, current_batch: int) -> int:
        util = self.get_gpu_utilization()
        if util < 0:
            return current_batch

        new_batch = current_batch
        if util < self.config.target_low:
            new_batch = min(self.config.max_batch, current_batch + self.config.step)
        elif util > self.config.target_high:
            new_batch = max(self.config.min_batch, current_batch - self.config.step)

        self.logger.info("GPU util=%.1f%% batch=%d->%d", util, current_batch, new_batch)
        return new_batch
