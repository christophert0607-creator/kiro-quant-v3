from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import torch

# 🔥 RTX 3060 Optimizations: Enable TF32 globally 🔥
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True

from v3_pipeline.core.history_priming import HistoryPrimer, PrimingConfig
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator

def _build_stderr_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger

def _write_histogram_svg(values: list[float], output_path: Path, title: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h, pad = 960, 420, 50
    if not values:
        output_path.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"><rect width="100%" height="100%" fill="#0d1117"/><text x="50%" y="50%" fill="#c9d1d9" text-anchor="middle">No data</text></svg>',
            encoding="utf-8",
        )
        return

    bins = min(20, max(5, int(math.sqrt(len(values)))))
    hist, edges = np.histogram(np.asarray(values), bins=bins)
    max_h = max(1, int(hist.max()))
    bar_w = (w - 2 * pad) / bins

    bars = []
    for i, c in enumerate(hist):
        x = pad + i * bar_w
        bh = (h - 2 * pad) * (c / max_h)
        y = h - pad - bh
        bars.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_w * 0.9:.2f}" height="{bh:.2f}" fill="#58a6ff"/>')

    x_min, x_max = float(edges[0]), float(edges[-1])
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">
  <rect width="100%" height="100%" fill="#0d1117"/>
  <line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" stroke="#8b949e"/>
  <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h-pad}" stroke="#8b949e"/>
  {''.join(bars)}
  <text x="{w/2}" y="26" text-anchor="middle" fill="#c9d1d9">{title}</text>
  <text x="{pad}" y="{h-14}" fill="#8b949e" font-size="12">min={x_min:.6f}</text>
  <text x="{w-pad}" y="{h-14}" fill="#8b949e" font-size="12" text-anchor="end">max={x_max:.6f}</text>
</svg>'''
    output_path.write_text(svg, encoding="utf-8")

@dataclass
class TrainerV42Config:
    storage_dir: Path = Path("v3_pipeline/data/storage/base_20y")
    reports_dir: Path = Path("v3_pipeline/reports")
    symbols: tuple[str, ...] = (
        "TSLA", "TSLL", "NVDA", "AAPL", "MSFT", "AMZN", "GOOG", "META", "NFLX", "AMD",
        "00700", "09988", "02800", "03690" # Top HK references
    )
    lookback: int = 60
    epochs: int = 50
    batch_size: int = 64
    accum_steps: int = 4 # Gradient accumulation: effective batch = batch_size * accum_steps
    lr: float = 1e-3
    grad_clip: float = 1.0
    early_stopping_patience: int = 15
    resume_ckpt: str = "" # Path to checkpoint to resume from

class TrainerV42GPU:
    def __init__(self, config: TrainerV42Config | None = None) -> None:
        self.config = config or TrainerV42Config()
        self.logger = _build_stderr_logger(self.__class__.__name__)
        self.config.storage_dir.mkdir(parents=True, exist_ok=True)
        self.config.reports_dir.mkdir(parents=True, exist_ok=True)
        self.feature_gen = TechnicalIndicatorGenerator()

    async def _integrity_check_and_repair(self) -> None:
        primer = HistoryPrimer(self.logger, PrimingConfig(days=7, interval="1m", retries=2, backoff_seconds=1.0))
        missing = []
        for s in self.config.symbols:
            p = self.config.storage_dir / f"{s}_1d.parquet"
            if not p.exists():
                missing.append(s)
                continue
            try:
                df = pd.read_parquet(p)
                if df.empty:
                    missing.append(s)
            except Exception:
                missing.append(s)

        if not missing:
            self.logger.info("Integrity check OK: no missing parquet in base_20y")
            return

        self.logger.warning("Integrity check found %d missing symbols, triggering HistoryPrimer", len(missing))
        repaired = await primer.prime_symbols(missing)
        for sym, df in repaired.items():
            if df.empty: continue
            out = self.config.storage_dir / f"{sym}_1d.parquet"
            normalized = df.copy()
            normalized["Date"] = pd.to_datetime(normalized["Date"], errors="coerce", utc=True).dt.tz_localize(None)
            normalized = normalized.dropna(subset=["Date"]).sort_values("Date").drop_duplicates("Date").reset_index(drop=True)
            normalized.to_parquet(out, index=False)
            self.logger.info("Repaired and wrote %s rows for %s", len(normalized), sym)

    @staticmethod
    def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True).dt.tz_localize(None)
        elif df.index.name in ("Date", "Datetime") or isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, errors="coerce", utc=True).tz_localize(None)
            df = df.reset_index().rename(columns={df.index.name or "index": "Date"})
        return df

    def _read_symbol_data(self, sym: str) -> pd.DataFrame | None:
        parquet_path = self.config.storage_dir / f"{sym}_1d.parquet"
        if parquet_path.exists():
            try:
                df = pd.read_parquet(parquet_path)
                return self._normalize_dates(df)
            except Exception as exc:
                self.logger.warning("Parquet read failed for %s (%s)", sym, exc)
        return None

    def _load_featured(self) -> dict[str, pd.DataFrame]:
        featured = {}
        for sym in self.config.symbols:
            df = self._read_symbol_data(sym)
            if df is None or df.empty:
                continue
            try:
                f = self.feature_gen.generate(df).ffill().bfill().dropna().reset_index(drop=True)
                if len(f) > self.config.lookback + 20:
                    featured[sym] = f
            except Exception as exc:
                self.logger.warning("Load/feature failed for %s: %s", sym, exc)
        return featured

    def _evaluate_symbol_mse(self, manager, symbol_df: pd.DataFrame) -> float:
        prep = manager.data_preparer
        if prep.feature_columns is None:
            return float("nan")

        work = symbol_df.copy()
        for col in prep.feature_columns:
            if col not in work.columns:
                work[col] = 0.0
        work = work[["Date", *prep.feature_columns]].copy()
        work[prep.feature_columns] = work[prep.feature_columns].ffill().bfill().fillna(0.0)

        try:
            x, y = prep.fit_transform(work)
        except Exception:
            return float("nan")

        manager.model.eval()
        with torch.no_grad():
            pred = manager.model(x.to(manager.device)).cpu().numpy().ravel()
            truth = y.numpy().ravel()
        return float(np.mean((pred - truth) ** 2)) if len(pred) else float("nan")

    async def run(self) -> dict:
        await self._integrity_check_and_repair()

        try:
            from torch.optim.lr_scheduler import CosineAnnealingLR
            from torch.utils.data import DataLoader, TensorDataset
            from v3_pipeline.models.manager import GlobalPretrainConfig, ModelManager
        except Exception as exc:
            self.logger.error(f"Dependency error: {exc}")
            return {"status": "error"}

        featured = self._load_featured()
        if not featured:
            self.logger.error("No data available.")
            return {"status": "no_data"}

        combined = pd.concat(featured.values(), ignore_index=True).sort_values("Date").reset_index(drop=True)
        split = int(len(combined) * 0.85)
        train_df, val_df = combined.iloc[:split], combined.iloc[split:]

        manager = ModelManager.build_global_pretraining_manager(
            sample_frame=train_df,
            config=GlobalPretrainConfig(storage_dir=self.config.storage_dir, lookback=self.config.lookback),
        )

        x_train, y_train = manager.data_preparer.fit_transform(train_df)
        x_val, y_val = manager.data_preparer.fit_transform(val_df if len(val_df) > self.config.lookback + 2 else train_df.tail(self.config.lookback + 100))

        train_loader = DataLoader(TensorDataset(x_train, y_train), batch_size=self.config.batch_size, shuffle=True)

        # 🔥 RTX 3060 Resume Checkpoint Mode
        if self.config.resume_ckpt and Path(self.config.resume_ckpt).exists():
           try:
               manager.model.load_state_dict(torch.load(self.config.resume_ckpt))
               self.logger.info(f"⏭️ Resumed successfully from {self.config.resume_ckpt}")
           except Exception as e:
               self.logger.warning(f"Failed to load checkpoint: {e}")

        # 🔥 Pytorch 2.0+ Acceleration via torch.compile
        if hasattr(torch, 'compile'):
            try:
                manager.model = torch.compile(manager.model)
                self.logger.info("🚀 torch.compile() is active.")
            except Exception as e:
                self.logger.warning(f"torch.compile() failed, fallback standard graph: {e}")

        criterion = torch.nn.MSELoss()
        optimizer = torch.optim.Adam(manager.model.parameters(), lr=self.config.lr)
        scheduler = CosineAnnealingLR(optimizer, T_max=max(2, self.config.epochs), eta_min=self.config.lr * 0.05)
        
        # 🔥 Mixed Precision Setup
        scaler = torch.amp.GradScaler('cuda', enabled=True)

        best_val = float("inf")
        best_state = None
        stale = 0
        train_losses, val_losses = []

        manager.model.train()
        for ep in range(1, self.config.epochs + 1):
            epoch_losses = []
            optimizer.zero_grad()
            
            for batch_idx, (bx, by) in enumerate(train_loader):
                bx, by = bx.to(manager.device), by.to(manager.device)
                
                # Mixed Precision context
                pt_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
                with torch.amp.autocast('cuda', dtype=pt_dtype, enabled=True):
                    pred = manager.model(bx)
                    loss = criterion(pred, by)
                    loss = loss / self.config.accum_steps # Gradient accumulation scale
                
                scaler.scale(loss).backward()
                
                if (batch_idx + 1) % self.config.accum_steps == 0 or (batch_idx + 1) == len(train_loader):
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(manager.model.parameters(), max_norm=self.config.grad_clip)
                    scaler.step(optimizer)
                    scaler.update()
                    optimizer.zero_grad()
                
                epoch_losses.append(float(loss.item() * self.config.accum_steps))

            scheduler.step()

            manager.model.eval()
            with torch.no_grad():
                vpred = manager.model(x_val.to(manager.device))
                vloss = float(criterion(vpred, y_val.to(manager.device)).item())
            manager.model.train()

            tloss = float(np.mean(epoch_losses)) if epoch_losses else float("nan")
            train_losses.append(tloss)
            val_losses.append(vloss)
            self.logger.info("Epoch %d/%d train=%.6f val=%.6f lr=%.6g", ep, self.config.epochs, tloss, vloss, scheduler.get_last_lr()[0])

            if vloss < best_val - 1e-8:
                best_val = vloss
                best_state = {k: v.cpu().clone() for k, v in getattr(manager.model, '_orig_mod', manager.model).state_dict().items()}
                stale = 0
                # Intermediate checkpoint save on best
                if ep % 5 == 0:
                   ckpt_tmp = manager.save(f"global_state_v4_2gpu_ep{ep}")
                   self.logger.info(f"Saved checkpoint => {ckpt_tmp}")
            else:
                stale += 1
                if stale >= self.config.early_stopping_patience:
                    self.logger.info("Early stopping at epoch %d", ep)
                    break

        if best_state is not None:
            getattr(manager.model, '_orig_mod', manager.model).load_state_dict(best_state)

        ckpt = manager.save("global_state_v4_2gpu_best")
        self.logger.info(f"🎉 Training cycle complete. Model saved at {ckpt}")

        mse_by_symbol = {}
        for sym, fdf in featured.items():
            mse = self._evaluate_symbol_mse(manager, fdf)
            if not np.isnan(mse): mse_by_symbol[sym] = mse

        _write_histogram_svg(list(mse_by_symbol.values()), self.config.reports_dir / "mse_gpu.svg", "GPU Avg MSE Dist")

        summary = {
            "status": "ok",
            "device": str(manager.device),
            "trained_symbols": sorted(featured.keys()),
            "best_val_mse": best_val,
            "train_losses": train_losses,
            "val_losses": val_losses,
            "checkpoint": str(ckpt)
        }
        (self.config.reports_dir / "training_summary_gpu.json").write_text(json.dumps(summary, indent=2))
        return summary

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--data-path", type=str, default="v3_pipeline/data/storage/base_20y")
    parser.add_argument("--resume", type=str, default="", help="Checkpoint path to resume")
    args = parser.parse_args()

    cfg = TrainerV42Config(
        epochs=args.epochs, 
        batch_size=args.batch,
        storage_dir=Path(args.data_path),
        resume_ckpt=args.resume
    )
    trainer = TrainerV42GPU(cfg)
    asyncio.run(trainer.run())

if __name__ == "__main__":
    main()
