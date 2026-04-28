#!/usr/bin/env python3
"""
Quick training script for V3 model using existing daily data.
Usage: python3 quick_train.py [--epochs 8] [--symbols TSLA NVDA AAPL MSFT META AMZN AMD PLTR GOOGL]
"""
import argparse, json, logging, sys
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", stream=sys.stderr)
logger = logging.getLogger("quick_train")

import torch
import pandas as pd
import numpy as np
from torch.utils.data import DataLoader, TensorDataset
from torch.optim.lr_scheduler import CosineAnnealingLR

from v3_pipeline.models.brain import KiroLSTM
from v3_pipeline.models.manager import DataPreparer, ModelManager
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator
from v3_pipeline.backtest.engine import BacktestConfig, BacktestEngine

STORAGE = Path("v3_pipeline/data/storage/base_20y")
MODEL_DIR = Path("v3_pipeline/models/trained_models")
MODEL_NAME = "v3_us_stocks"

US_SYMBOLS = ["TSLA", "NVDA", "AAPL", "MSFT", "META", "AMZN", "AMD", "PLTR", "GOOGL", "F", "COIN", "NFLX"]


def load_and_feature(symbol: str, interval: str = "1d") -> pd.DataFrame:
    path = STORAGE / f"{symbol}_{interval}.parquet"
    if not path.exists():
        logger.warning("No data for %s", symbol)
        return pd.DataFrame()
    df = pd.read_parquet(path)
    gen = TechnicalIndicatorGenerator()
    feat = gen.generate(df)
    feat = feat.ffill().bfill().dropna().reset_index(drop=True)
    logger.info("Loaded %s: %d rows, %d cols", symbol, len(feat), len(feat.columns))
    return feat


def train(symbols, epochs=8, lookback=60, hidden_dim=96, batch_size=64, lr=1e-3):
    featured = {}
    for sym in symbols:
        df = load_and_feature(sym)
        if df.empty or len(df) < lookback + 20:
            continue
        featured[sym] = df
    
    if not featured:
        logger.error("No data loaded for any symbol!")
        return None
    
    logger.info("Training on %d symbols, total %d rows", len(featured), sum(len(df) for df in featured.values()))
    
    # Combine all symbols
    combined = pd.concat(featured.values(), ignore_index=True).sort_values("Date").reset_index(drop=True)
    logger.info("Combined dataset: %d rows, %d columns", len(combined), len(combined.columns))
    
    # Prepare data
    preparer = DataPreparer(lookback=lookback, target_col="Close")
    x, y = preparer.fit_transform(combined)
    logger.info("Tensor shapes: X=%s y=%s", x.shape, y.shape)
    
    # Create model
    model = KiroLSTM(input_dim=x.shape[-1], hidden_dim=hidden_dim, num_layers=2, dropout=0.2, output_dim=1)
    manager = ModelManager(model=model, data_preparer=preparer)
    logger.info("Model: %s", model)
    logger.info("Device: %s", manager.device)
    
    # Train
    dl = DataLoader(TensorDataset(x, y), batch_size=batch_size, shuffle=True)
    criterion = torch.nn.MSELoss()
    optimizer = torch.optim.Adam(manager.model.parameters(), lr=lr)
    scheduler = CosineAnnealingLR(optimizer, T_max=max(2, epochs), eta_min=lr * 0.05)
    
    manager.model.train()
    losses = []
    for ep in range(1, epochs + 1):
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
        logger.info("Epoch %d/%d loss=%.6f lr=%.6g", ep, epochs, ep_loss, scheduler.get_last_lr()[0])
    
    # Save
    ckpt = manager.save(MODEL_NAME)
    logger.info("Saved model to %s", ckpt)
    
    # Quick backtest on first symbol
    bt_sym = next(iter(featured))
    bt_df = featured[bt_sym]
    bt_engine = BacktestEngine(BacktestConfig())
    bt_result = bt_engine.run(bt_df, manager)
    logger.info("Backtest on %s: %s", bt_sym, bt_result)
    
    # Write metrics
    metrics = {
        "trained_symbols": sorted(featured.keys()),
        "total_rows": int(len(combined)),
        "epochs": epochs,
        "final_loss": float(losses[-1]),
        "losses": [float(l) for l in losses],
        "checkpoint": str(ckpt),
        "backtest": bt_result,
        "config": {
            "lookback": lookback,
            "hidden_dim": hidden_dim,
            "batch_size": batch_size,
            "lr": lr,
        }
    }
    metrics_path = MODEL_DIR / f"{MODEL_NAME}_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2))
    logger.info("Metrics written to %s", metrics_path)
    
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--symbols", nargs="*", default=US_SYMBOLS)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--hidden-dim", type=int, default=96)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    
    result = train(args.symbols, epochs=args.epochs, lookback=args.lookback, hidden_dim=args.hidden_dim, batch_size=args.batch_size)
    if result:
        print(json.dumps(result, indent=2))
