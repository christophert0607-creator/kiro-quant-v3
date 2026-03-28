#!/usr/bin/env python3
"""Continuous learning pipeline for meta-labeling model.

Runs end-to-end pipeline: extract → join → enrich → label → train → export.
Maintains versioned model archive for rollback capability.

Safe: read-only to broker; purely offline.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
            if isinstance(o, dict):
                rows.append(o)
        except Exception:
            continue
    return rows


def run_step(script: str, args: list[str], env: dict[str, str] | None = None) -> bool:
    import subprocess
    cmd = ["python3", f"dev/meta_labeling/{script}"] + args
    print(f"\n>>> Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        print(f"STDERR: {result.stderr}")
        print(f"STDOUT: {result.stdout}")
        return False
    print(result.stdout)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Continuous learning for meta-labeling")
    ap.add_argument("--dry-run", action="store_true", help="Skip training, just run pipeline up to labels")
    ap.add_argument("--keep-previous", action="store_true", help="Do not overwrite previous model, archive instead")
    args = ap.parse_args()

    ts = now_iso()
    models_dir = Path("dev/meta_labeling/models")
    archive_dir = models_dir / "archive"
    archive_dir.mkdir(exist_ok=True)

    # Step 1: Extract decisions + snapshots
    print("\n=== Step 1: Extract ===")
    if not run_step("dataset_extractor.py", ["--join"]):
        raise RuntimeError("Step 1 failed: dataset_extractor --join")

    # Step 2: Enrich with OHLCV + indicators
    print("\n=== Step 2: Enrich ===")
    joined = Path("dev/meta_labeling/out/joined_events.jsonl")
    enriched = Path("dev/meta_labeling/out/enriched_events.jsonl")
    if not run_step("dataset_extractor.py", [
        "--enrich",
        "--joined", str(joined),
        "--out", str(enriched)
    ]):
        raise RuntimeError("Step 2 failed: dataset_extractor --enrich")

    # Step 3: Generate labels
    print("\n=== Step 3: Label ===")
    if not run_step("label_generator.py", []):
        raise RuntimeError("Step 3 failed: label_generator")

    # Check labeled events count
    labeled_path = Path("dev/meta_labeling/out/labeled_events.jsonl")
    labeled = load_jsonl(labeled_path)
    n_positive = sum(1 for r in labeled if r.get("label") == 1)
    print(f"Labeled events: {len(labeled)} (positive: {n_positive})")

    if args.dry_run:
        print("\n[Dry-run] Skipping training")
        return 0

    # Step 4: Train model (uses baseline_model.py with real labels)
    print("\n=== Step 4: Train ===")
    if not run_step("baseline_model.py", []):
        raise RuntimeError("Step 4 failed: baseline_model")

    # Step 5: Export to joblib
    print("\n=== Step 5: Export ===")
    if not run_step("export_joblib.py", []):
        raise RuntimeError("Step 5 failed: export_joblib")

    # Archive previous model if keep-previous
    if args.keep_previous:
        current = models_dir / "meta_model.joblib"
        current_meta = models_dir / "meta_model_meta.json"
        if current.exists() or current_meta.exists():
            archive_name = f"meta_model_{ts}.joblib"
            archive_meta = f"meta_model_meta_{ts}.json"
            if current.exists():
                shutil.copy(current, archive_dir / archive_name)
                print(f"Archived: {archive_dir / archive_name}")
            if current_meta.exists():
                shutil.copy(current_meta, archive_dir / archive_meta)
                print(f"Archived: {archive_dir / archive_meta}")

    # Write lineage record
    lineage_path = models_dir / "lineage.jsonl"
    lineage_entry = {
        "timestamp": ts,
        "n_labeled": len(labeled),
        "n_positive": n_positive,
        "model_file": "meta_model.joblib",
        "meta_file": "meta_model_meta.json",
        "pipeline": "continuous_learning"
    }
    with lineage_path.open("a") as f:
        f.write(json.dumps(lineage_entry, ensure_ascii=False) + "\n")

    print(f"\n=== Pipeline complete at {ts} ===")
    print(f"Labeled: {len(labeled)} events, {n_positive} positive")
    print(f"Model: {models_dir / 'meta_model.joblib'}")
    print(f"Lineage: {lineage_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())