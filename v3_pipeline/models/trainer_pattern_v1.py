from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from torch import nn
from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.utils.data import DataLoader, Dataset

from v3_pipeline.features.indicators import TechnicalIndicatorGenerator
from v3_pipeline.models.brain import StockPatternModel

PATTERN_LABELS = [
    "Unknown",
    "HeadShoulderTop",
    "DoubleBottom",
    "UpTrend",
    "DownTrend",
    "Reversal",
    "VolumePulse",
    "Triangle",
]


def _build_stderr_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def generate_pattern_label(window: pd.DataFrame) -> int:
    """Rule-based pseudo labeling for historical pattern bootstrapping."""
    c = window["Close"].astype(float).values
    h = window["High"].astype(float).values
    l = window["Low"].astype(float).values
    v = window["Volume"].astype(float).values

    if len(c) < 20:
        return 0

    slope = np.polyfit(np.arange(len(c)), c, 1)[0]
    volume_spike = v[-1] > np.nanmean(v[-10:]) * 1.8

    # crude head-and-shoulders top: three peaks with middle highest + latest breakdown
    p1, p2, p3 = np.percentile(h[: len(h) // 3], 90), np.percentile(h[len(h) // 3 : 2 * len(h) // 3], 90), np.percentile(h[2 * len(h) // 3 :], 90)
    if p2 > p1 * 1.01 and p2 > p3 * 1.01 and c[-1] < np.nanmean(c[-5:]):
        return 1

    # double bottom: two local lows near each other + rebound
    low1 = np.nanmin(l[: len(l) // 2])
    low2 = np.nanmin(l[len(l) // 2 :])
    if low1 > 0 and abs(low1 - low2) / low1 < 0.02 and c[-1] > np.nanmean(c[-5:]):
        return 2

    if slope > 0.0:
        return 3
    if slope < 0.0:
        return 4

    if np.sign(c[-1] - c[-5]) != np.sign(c[-6] - c[-10]):
        return 5
    if volume_spike:
        return 6

    span = (np.nanmax(h[-20:]) - np.nanmin(l[-20:])) / max(np.nanmean(c[-20:]), 1e-9)
    if span < 0.04:
        return 7
    return 0


class PatternWindowDataset(Dataset):
    def __init__(self, frame: pd.DataFrame, lookback: int = 60, feature_cols: list[str] | None = None) -> None:
        self.lookback = lookback
        self.frame = frame.copy().reset_index(drop=True)
        self.feature_cols = feature_cols or [
            c
            for c in ["Open", "High", "Low", "Close", "Volume", "MA_20", "MA_50", "RSI_14", "BB_upper", "BB_lower"]
            if c in self.frame.columns
        ]
        if not self.feature_cols:
            raise ValueError("No usable feature columns")

        feat = self.frame[self.feature_cols].astype(float).ffill().bfill().fillna(0.0)
        mins = feat.min(axis=0)
        maxs = feat.max(axis=0)
        denom = (maxs - mins).replace(0, 1.0)
        self.features = ((feat - mins) / denom).clip(0.0, 1.0).values.astype(np.float32)

        closes = self.frame["Close"].astype(float).ffill().bfill().values
        self.targets = closes.astype(np.float32)

    def __len__(self) -> int:
        return max(0, len(self.frame) - self.lookback)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        end = idx + self.lookback
        x = self.features[idx:end]
        price_now = self.targets[end - 1]
        price_next = self.targets[end]
        y_reg = np.float32((price_next - price_now) / max(abs(price_now), 1e-6))
        y_cls = np.int64(generate_pattern_label(self.frame.iloc[idx:end]))
        return torch.tensor(x), torch.tensor([y_reg]), torch.tensor(y_cls)


@dataclass
class PatternTrainerConfig:
    storage_dir: Path = Path("v3_pipeline/data/storage/base_10y")
    output_dir: Path = Path("v3_pipeline/models/trained_models")
    reports_dir: Path = Path("v3_pipeline/reports")
    lookback: int = 60
    batch_size: int = 64
    epochs: int = 20
    lr: float = 1e-3
    grad_clip: float = 1.0
    patience: int = 6


class PatternTrainerV1:
    def __init__(self, cfg: PatternTrainerConfig | None = None) -> None:
        self.cfg = cfg or PatternTrainerConfig()
        self.logger = _build_stderr_logger(self.__class__.__name__)
        self.feature_gen = TechnicalIndicatorGenerator()

    def _load_parquet(self) -> pd.DataFrame:
        frames = []
        for pq in sorted(self.cfg.storage_dir.glob("*.parquet")):
            try:
                df = pd.read_parquet(pq)
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                self.logger.warning("skip %s: %s", pq.name, exc)
        if not frames:
            raise RuntimeError(f"No parquet data under {self.cfg.storage_dir}")
        merged = pd.concat(frames, ignore_index=True)
        merged["Date"] = pd.to_datetime(merged["Date"], errors="coerce")
        merged = merged.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
        featured = self.feature_gen.generate(merged).ffill().bfill().dropna().reset_index(drop=True)
        return featured

    def run(self) -> dict:
        frame = self._load_parquet()
        dataset = PatternWindowDataset(frame, lookback=self.cfg.lookback)
        split = int(len(dataset) * 0.85)
        train_set, val_set = torch.utils.data.random_split(dataset, [split, len(dataset) - split])

        train_loader = DataLoader(train_set, batch_size=self.cfg.batch_size, shuffle=True)
        val_loader = DataLoader(val_set, batch_size=self.cfg.batch_size, shuffle=False)

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = StockPatternModel(input_dim=len(dataset.feature_cols), num_patterns=len(PATTERN_LABELS)).to(device)

        optimizer = torch.optim.AdamW(model.parameters(), lr=self.cfg.lr)
        scheduler = CosineAnnealingLR(optimizer, T_max=max(self.cfg.epochs, 2), eta_min=self.cfg.lr * 0.05)
        mse_loss = nn.MSELoss()
        ce_loss = nn.CrossEntropyLoss()

        best_val = float("inf")
        stale = 0
        best_state = None
        train_losses: list[float] = []
        val_losses: list[float] = []

        for epoch in range(1, self.cfg.epochs + 1):
            model.train()
            e_losses = []
            for x, y_reg, y_cls in train_loader:
                x, y_reg, y_cls = x.to(device), y_reg.to(device), y_cls.to(device)
                optimizer.zero_grad()
                reg_pred, cls_logits = model(x)
                loss_reg = mse_loss(reg_pred, y_reg)
                loss_cls = ce_loss(cls_logits, y_cls)
                loss = 0.7 * loss_reg + 0.3 * loss_cls
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), self.cfg.grad_clip)
                optimizer.step()
                e_losses.append(float(loss.item()))

            scheduler.step()
            train_loss = float(np.mean(e_losses)) if e_losses else float("nan")
            train_losses.append(train_loss)

            model.eval()
            v_losses = []
            with torch.no_grad():
                for x, y_reg, y_cls in val_loader:
                    x, y_reg, y_cls = x.to(device), y_reg.to(device), y_cls.to(device)
                    reg_pred, cls_logits = model(x)
                    loss = 0.7 * mse_loss(reg_pred, y_reg) + 0.3 * ce_loss(cls_logits, y_cls)
                    v_losses.append(float(loss.item()))
            val_loss = float(np.mean(v_losses)) if v_losses else float("nan")
            val_losses.append(val_loss)
            self.logger.info("Epoch %d/%d train=%.6f val=%.6f", epoch, self.cfg.epochs, train_loss, val_loss)

            if val_loss < best_val - 1e-8:
                best_val = val_loss
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
                stale = 0
            else:
                stale += 1
                if stale >= self.cfg.patience:
                    self.logger.info("Early stopping at epoch %d", epoch)
                    break

        if best_state is not None:
            model.load_state_dict(best_state)

        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        out = self.cfg.output_dir / "global_state_pattern_v1.pth"
        torch.save(
            {
                "model_state_dict": model.state_dict(),
                "lookback": self.cfg.lookback,
                "feature_columns": dataset.feature_cols,
                "pattern_labels": PATTERN_LABELS,
                "train_losses": train_losses,
                "val_losses": val_losses,
            },
            out,
        )

        summary = {
            "status": "ok",
            "device": str(device),
            "checkpoint": str(out),
            "epochs_ran": len(train_losses),
            "best_val": best_val,
            "train_losses": train_losses,
            "val_losses": val_losses,
        }
        self.cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        (self.cfg.reports_dir / "pattern_training_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary


def train_one_epoch_minimal(parquet_or_csv: str, lookback: int = 60) -> dict:
    """Minimal reproduction script entry: train 1 epoch quickly."""
    path = Path(parquet_or_csv)
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    else:
        frame = pd.read_parquet(path)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame = frame.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    frame = TechnicalIndicatorGenerator().generate(frame).ffill().bfill().dropna().reset_index(drop=True)

    dataset = PatternWindowDataset(frame, lookback=lookback)
    loader = DataLoader(dataset, batch_size=32, shuffle=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = StockPatternModel(input_dim=len(dataset.feature_cols), num_patterns=len(PATTERN_LABELS)).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)

    mse_loss = nn.MSELoss()
    ce_loss = nn.CrossEntropyLoss()

    model.train()
    for x, y_reg, y_cls in loader:
        x, y_reg, y_cls = x.to(device), y_reg.to(device), y_cls.to(device)
        opt.zero_grad()
        reg_pred, cls_logits = model(x)
        loss = 0.7 * mse_loss(reg_pred, y_reg) + 0.3 * ce_loss(cls_logits, y_cls)
        loss.backward()
        opt.step()
        return {"status": "ok", "loss": float(loss.item()), "device": str(device), "samples": len(dataset)}
    return {"status": "no_sample"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--lookback", type=int, default=60)
    args = parser.parse_args()

    trainer = PatternTrainerV1(PatternTrainerConfig(epochs=args.epochs, lookback=args.lookback))
    trainer.run()


if __name__ == "__main__":
    main()
