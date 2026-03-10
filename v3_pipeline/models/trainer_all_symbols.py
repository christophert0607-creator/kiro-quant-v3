from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from v3_pipeline.utils.gpu_monitor import GPUMonitor


def _scan_base_files(base_dir: Path) -> list[Path]:
    return sorted(base_dir.glob("*_10y_base.parquet"))


def _prepare_combined_frame(files: list[Path]) -> pd.DataFrame:
    frames = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            if not df.empty:
                frames.append(df)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    if "Date" in combined.columns:
        combined["Date"] = pd.to_datetime(combined["Date"], errors="coerce")
        combined = combined.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    return combined


def run_once(base_dir: Path, epochs: int = 10, init_batch_size: int = 256) -> dict:
    monitor = GPUMonitor()
    files = _scan_base_files(base_dir)
    combined = _prepare_combined_frame(files)
    if combined.empty:
        return {"status": "no_data", "files": 0}

    try:
        from v3_pipeline.models.manager import GlobalPretrainConfig, ModelManager
    except Exception as exc:
        return {"status": f"missing_dependency:{exc}", "files": len(files), "rows": int(len(combined))}

    manager = ModelManager.build_global_pretraining_manager(
        sample_frame=combined,
        config=GlobalPretrainConfig(storage_dir=base_dir, lookback=60),
    )
    batch_size = monitor.adjust_batch_size(init_batch_size)

    dataloader = manager.build_dataloader(combined, batch_size=batch_size, shuffle=True)

    import torch
    criterion = torch.nn.MSELoss()
    optimizer = torch.optim.Adam(manager.model.parameters(), lr=1e-3)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(2, epochs), eta_min=5e-5)

    best_loss = float("inf")
    stale = 0
    patience = 15
    losses = []

    for ep in range(1, epochs + 1):
        ep_losses = []
        manager.model.train()
        for bx, by in dataloader:
            bx = bx.to(manager.device)
            by = by.to(manager.device)
            optimizer.zero_grad()
            pred = manager.model(bx)
            loss = criterion(pred, by)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(manager.model.parameters(), 1.0)
            optimizer.step()
            ep_losses.append(float(loss.item()))

        scheduler.step()
        mean_loss = float(sum(ep_losses) / max(1, len(ep_losses)))
        losses.append(mean_loss)

        if mean_loss < best_loss - 1e-8:
            best_loss = mean_loss
            stale = 0
            manager.save("global_all_symbols_best")
        else:
            stale += 1
            if stale >= patience:
                break

        batch_size = monitor.adjust_batch_size(batch_size)

    report = {
        "status": "ok",
        "files": len(files),
        "rows": int(len(combined)),
        "epochs_ran": len(losses),
        "best_loss": best_loss,
        "losses": losses,
    }
    Path("v3_pipeline/reports").mkdir(parents=True, exist_ok=True)
    Path("v3_pipeline/reports/all_symbols_training.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default="v3_pipeline/data/storage/base_10y")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--every-hours", type=float, default=4.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    if args.once:
        print(run_once(base_dir=base_dir, epochs=args.epochs))
        return

    while True:
        print(run_once(base_dir=base_dir, epochs=args.epochs))
        time.sleep(max(60.0, args.every_hours * 3600.0))


if __name__ == "__main__":
    main()
