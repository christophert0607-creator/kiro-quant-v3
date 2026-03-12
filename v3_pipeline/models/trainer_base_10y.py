from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


def _build_stderr_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _write_loss_svg(losses: list[float], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h, pad = 900, 360, 40
    if not losses:
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"><rect width="100%" height="100%" fill="#0d1117"/><text x="50%" y="50%" fill="#c9d1d9" text-anchor="middle">No loss data</text></svg>'
        output_path.write_text(svg, encoding="utf-8")
        return
    min_l, max_l = min(losses), max(losses)
    span = max(max_l - min_l, 1e-9)
    pts = []
    for i, v in enumerate(losses):
        x = pad + (w - 2 * pad) * (i / max(1, len(losses) - 1))
        y = h - pad - (h - 2 * pad) * ((v - min_l) / span)
        pts.append(f"{x:.2f},{y:.2f}")
    poly = " ".join(pts)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">
  <rect width="100%" height="100%" fill="#0d1117"/>
  <line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" stroke="#8b949e"/>
  <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h-pad}" stroke="#8b949e"/>
  <polyline fill="none" stroke="#58a6ff" stroke-width="2" points="{poly}"/>
  <text x="{w/2}" y="24" text-anchor="middle" fill="#c9d1d9">Kiro Global State 10Y Loss Curve</text>
</svg>'''
    output_path.write_text(svg, encoding="utf-8")


@dataclass
class Base10yConfig:
    storage_dir: Path = Path("v3_pipeline/data/storage/base_10y")
    output_dir: Path = Path("v3_pipeline/reports")
    symbols: tuple[str, ...] = ("TSLA", "TSLL", "NVDA", "AAPL", "MSFT", "AMZN", "GOOG", "META")
    start_date: str = "2015-01-01"
    end_date: str = "2025-01-01"
    interval: str = "1d"
    epochs: int = 8
    batch_size: int = 64
    lr: float = 1e-3
    lookback: int = 60


class Base10yTrainer:
    def __init__(self, config: Base10yConfig | None = None) -> None:
        self.config = config or Base10yConfig()
        self.logger = _build_stderr_logger(self.__class__.__name__)
        self.config.storage_dir.mkdir(parents=True, exist_ok=True)
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

    def _load_or_fetch_symbol(self, symbol: str) -> pd.DataFrame:
        from v3_pipeline.data.downloader import DownloadConfig, HistoricalDataDownloader

        parquet = self.config.storage_dir / f"{symbol}_{self.config.interval}.parquet"
        if parquet.exists():
            df = pd.read_parquet(parquet)
            if not df.empty:
                return df

        downloader = HistoricalDataDownloader(DownloadConfig(storage_dir=self.config.storage_dir, auto_save=False))
        try:
            df = downloader.fetch_history(
                symbol=symbol,
                start_date=self.config.start_date,
                end_date=self.config.end_date,
                interval=self.config.interval,
                save=False,
            )
        except Exception as exc:
            self.logger.warning("Fetch failed for %s: %s", symbol, exc)
            return pd.DataFrame()

        if not df.empty:
            df.to_parquet(parquet, index=False)
        return df

    def _prepare_featured(self, symbols: Iterable[str]) -> dict[str, pd.DataFrame]:
        from v3_pipeline.features.indicators import TechnicalIndicatorGenerator

        feature_gen = TechnicalIndicatorGenerator()
        out: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            raw = self._load_or_fetch_symbol(sym)
            if raw.empty:
                self.logger.warning("No data for %s", sym)
                continue
            feat = feature_gen.generate(raw)
            feat = feat.ffill().bfill().dropna().reset_index(drop=True)
            if len(feat) < self.config.lookback + 20:
                self.logger.warning("Insufficient featured rows for %s: %d", sym, len(feat))
                continue
            out[sym] = feat
            self.logger.info("Loaded %s with %d featured rows", sym, len(feat))
        return out

    def train_global_state(self) -> dict:
        metrics = {
            "status": "ok",
            "trained_symbols": [],
            "combined_rows": 0,
            "losses": [],
            "loss_curve_svg": str(self.config.output_dir / "loss_curve_10y.svg"),
            "checkpoint": None,
            "training_win_rate": 0.0,
            "backtest_symbol": None,
            "backtest_summary": {},
            "device": "unknown",
        }

        featured_by_symbol = self._prepare_featured(self.config.symbols)
        if not featured_by_symbol:
            metrics["status"] = "no_data"
            _write_loss_svg([], Path(metrics["loss_curve_svg"]))
            (self.config.output_dir / "base_10y_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            return metrics

        try:
            import torch
            from torch.optim.lr_scheduler import CosineAnnealingLR
            from torch.utils.data import DataLoader, TensorDataset

            from v3_pipeline.backtest.engine import BacktestConfig, BacktestEngine
            from v3_pipeline.models.brain import KiroLSTM
            from v3_pipeline.models.manager import DataPreparer, ModelManager, TrainingConfig
        except Exception as exc:
            metrics["status"] = f"missing_dependency:{exc}"
            metrics["trained_symbols"] = sorted(featured_by_symbol.keys())
            metrics["combined_rows"] = int(sum(len(df) for df in featured_by_symbol.values()))
            _write_loss_svg([], Path(metrics["loss_curve_svg"]))
            (self.config.output_dir / "base_10y_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            self.logger.warning("Training skipped due to dependency issue: %s", exc)
            return metrics

        combined = pd.concat(featured_by_symbol.values(), ignore_index=True)
        combined["Date"] = pd.to_datetime(combined["Date"], errors="coerce", utc=True).dt.tz_convert(None)
        combined = combined.sort_values("Date").reset_index(drop=True)

        train_cfg = TrainingConfig(
            lookback=self.config.lookback,
            epochs=self.config.epochs,
            batch_size=self.config.batch_size,
            lr=self.config.lr,
            hidden_dim=96,
            num_layers=2,
            dropout=0.2,
        )

        preparer = DataPreparer(lookback=train_cfg.lookback, target_col=train_cfg.target_col)
        x, y = preparer.fit_transform(combined)

        model = KiroLSTM(
            input_dim=x.shape[-1],
            hidden_dim=train_cfg.hidden_dim,
            num_layers=train_cfg.num_layers,
            dropout=train_cfg.dropout,
            output_dim=train_cfg.output_dim,
        )
        manager = ModelManager(model=model, data_preparer=preparer)
        metrics["device"] = str(manager.device)

        dl = DataLoader(TensorDataset(x, y), batch_size=train_cfg.batch_size, shuffle=True)
        criterion = torch.nn.MSELoss()
        optimizer = torch.optim.Adam(manager.model.parameters(), lr=train_cfg.lr)
        scheduler = CosineAnnealingLR(optimizer, T_max=max(2, train_cfg.epochs), eta_min=train_cfg.lr * 0.05)

        losses: list[float] = []
        manager.model.train()
        for ep in range(1, train_cfg.epochs + 1):
            ep_losses = []
            for bx, by in dl:
                bx = bx.to(manager.device)
                by = by.to(manager.device)
                optimizer.zero_grad()
                pred = manager.model(bx)
                loss = criterion(pred, by)
                loss.backward()
                optimizer.step()
                ep_losses.append(float(loss.item()))
            scheduler.step()
            ep_loss = float(sum(ep_losses) / max(1, len(ep_losses)))
            losses.append(ep_loss)
            self.logger.info("Global pretrain epoch %d/%d loss=%.6f lr=%.6g", ep, train_cfg.epochs, ep_loss, scheduler.get_last_lr()[0])

        ckpt = manager.save("global_state_10y")

        backtest_symbol = next(iter(featured_by_symbol.keys()))
        bt_engine = BacktestEngine(BacktestConfig())
        bt_summary = bt_engine.run(featured_by_symbol[backtest_symbol], manager)

        wins = sum(1 for l1, l2 in zip(losses[:-1], losses[1:]) if l2 <= l1)
        win_rate = float(wins / max(1, len(losses) - 1))

        _write_loss_svg(losses, Path(metrics["loss_curve_svg"]))

        metrics.update(
            {
                "trained_symbols": sorted(featured_by_symbol.keys()),
                "combined_rows": int(len(combined)),
                "losses": losses,
                "checkpoint": str(ckpt),
                "training_win_rate": win_rate,
                "backtest_symbol": backtest_symbol,
                "backtest_summary": bt_summary,
            }
        )
        metrics_path = self.config.output_dir / "base_10y_metrics.json"
        metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        self.logger.info("Wrote metrics to %s", metrics_path)
        return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--symbols", nargs="*", default=list(Base10yConfig().symbols))
    args = parser.parse_args()

    cfg = Base10yConfig(epochs=args.epochs, symbols=tuple(args.symbols))
    trainer = Base10yTrainer(cfg)
    trainer.train_global_state()


if __name__ == "__main__":
    main()
