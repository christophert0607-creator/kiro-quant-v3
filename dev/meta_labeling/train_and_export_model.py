#!/usr/bin/env python3
"""Train & export a baseline meta-labeling model artifact (offline).

This script is for option (2): build a reproducible training pipeline.

Inputs:
- dev/meta_labeling/out/joined_events.jsonl  (from dataset_extractor --join)

Outputs:
- dev/meta_labeling/models/meta_model.joblib
- dev/meta_labeling/models/meta_model_meta.json  (feature list + threshold)

Notes:
- Currently uses a simple heuristic label because true forward-return labels are not
  yet implemented. It will only train if enough BUY_PLACED events exist.
- Once labels are available, replace `make_labels()` accordingly.

Safe: read-only to broker; purely offline.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FEATURES = [
    "prediction",
    "confidence",
    "sentiment_score",
    "mc_win_rate",
    "mc_var95",
    "risk_pct",
    "rr",
    "threshold",
]


def load_joined(path: Path) -> list[dict[str, Any]]:
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


def build_X(rows: list[dict[str, Any]]):
    import pandas as pd  # type: ignore

    data = []
    for r in rows:
        if str(r.get("action")) not in {"BUY_PLACED", "SELL_PLACED"} and not str(r.get("action", "")).startswith("BUY_BLOCKED"):
            continue
        # keep only if core features exist (fallback to 0)
        data.append({f: r.get(f, 0) for f in FEATURES} | {"action": r.get("action"), "symbol": r.get("symbol")})

    df = pd.DataFrame(data)
    for f in FEATURES:
        df[f] = pd.to_numeric(df[f], errors="coerce").fillna(0.0)
    return df


def make_labels(df):
    """TEMP label: treat BUY_PLACED as positive, BUY_BLOCKED_* as negative.

    This is a placeholder until proper fwd-return labels are implemented.
    """
    import numpy as np  # type: ignore

    y = df["action"].astype(str).apply(lambda a: 1 if a == "BUY_PLACED" else 0).to_numpy(dtype=int)
    return y


def train(df):
    import numpy as np  # type: ignore
    from sklearn.linear_model import LogisticRegression  # type: ignore
    from sklearn.pipeline import Pipeline  # type: ignore
    from sklearn.preprocessing import StandardScaler  # type: ignore

    X = df[FEATURES].to_numpy(dtype=float)
    y = make_labels(df)

    # Require enough positives
    if int(y.sum()) < 20:
        raise RuntimeError(f"Not enough BUY_PLACED positives to train: positives={int(y.sum())}, total={len(y)}")

    clf = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(max_iter=200, class_weight="balanced")),
        ]
    )
    clf.fit(X, y)
    return clf


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", default="dev/meta_labeling/out/joined_events.jsonl")
    ap.add_argument("--out-dir", default="dev/meta_labeling/models")
    ap.add_argument("--threshold", type=float, default=0.5)
    args = ap.parse_args()

    joined_path = Path(args.joined)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_joined(joined_path)
    if not rows:
        raise RuntimeError(f"joined_events is empty: {joined_path}")

    df = build_X(rows)
    if df.empty:
        raise RuntimeError("no usable rows in joined_events")

    model = train(df)

    # Export
    try:
        import joblib  # type: ignore

        joblib_path = out_dir / "meta_model.joblib"
        joblib.dump({"model": model, "feature_names": FEATURES}, joblib_path)
    except Exception as exc:
        raise RuntimeError(f"joblib export failed: {exc}")

    meta = {
        "schema": "kiro.meta_model.meta.v1",
        "feature_names": FEATURES,
        "threshold": float(args.threshold),
        "joined_source": str(joined_path),
        "rows": int(len(df)),
        "note": "TEMP label is placeholder; replace with forward-return labeling before real evaluation.",
    }
    (out_dir / "meta_model_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")

    print(f"exported: {out_dir / 'meta_model.joblib'}")
    print(f"meta: {out_dir / 'meta_model_meta.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
