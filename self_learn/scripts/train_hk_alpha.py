"""HKAlpha-1 trainer: walk-forward GBM + isotonic calibration.

Trains the two HK prediction V2 heads on intraday bars:

    ret_head  — regression on triple-barrier forward return
    prob_head — classification on "hit TP before SL"

Data contract: dict of featured OHLCV frames (one per symbol, indicator
columns included, e.g. straight out of kiro_quant.db market_data). Index
symbols 2800.HK / 3033.HK, when present, are consumed as market-context
inputs rather than trained on.

Promotion guard (design §2.3): the artifact is only written when
  - sessions >= min_sessions and rows >= min_rows
  - holdout directional accuracy >= 0.55
  - prob_head AUC >= 0.55 and calibrated Brier <= 0.25
A blocked run prints `status=blocked reason=...` and writes nothing.

CLI:
    PYTHONPATH=. python3 self_learn/scripts/train_hk_alpha.py --dry-run
    PYTHONPATH=. python3 self_learn/scripts/train_hk_alpha.py --sessions 60
"""

from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from self_learn.triple_barrier import label_triple_barrier
from v3_pipeline.models.hk_alpha_features import FEATURE_ORDER, build_hk_alpha_features

try:
    import lightgbm as lgb
except ImportError:
    lgb = None
try:
    import xgboost as xgb
except ImportError:
    xgb = None

CONTEXT_SYMBOLS = ("2800.HK", "3033.HK")
ARTIFACT_PREFIX = "hkalpha1"
MIN_BARS_PER_SESSION = 50  # below this a (symbol, date) is daily/partial data, not an intraday session

COLUMN_ALIASES = {  # market_data uses lowercase OHLCV, live buffers use capitalized
    "timestamp": "Date", "open": "Open", "high": "High",
    "low": "Low", "close": "Close", "volume": "Volume",
}


@dataclass
class TrainHKAlphaConfig:
    tp: float = 0.02
    sl: float = 0.02
    max_bars: int = 30
    n_folds: int = 5
    embargo_bars: int = 30
    min_sessions: int = 60
    min_rows: int = 5000
    min_dir_acc: float = 0.55
    min_auc: float = 0.55
    max_brier: float = 0.25
    seed: int = 7
    output_dir: str = "self_learn/models"


@dataclass
class TrainResult:
    status: str                      # "ok" | "blocked"
    reason: str = ""
    metrics: dict = field(default_factory=dict)
    artifact_path: str | None = None
    sidecar_path: str | None = None


def _gbm_backend() -> str:
    if lgb is not None:
        return "lightgbm"
    if xgb is not None:
        return "xgboost"
    return "sklearn"


def _make_regressor(seed: int):
    backend = _gbm_backend()
    if backend == "lightgbm":
        return lgb.LGBMRegressor(n_estimators=200, max_depth=6, learning_rate=0.05,
                                 random_state=seed, verbose=-1)
    if backend == "xgboost":
        return xgb.XGBRegressor(n_estimators=200, max_depth=6, learning_rate=0.05,
                                random_state=seed, verbosity=0)
    from sklearn.ensemble import GradientBoostingRegressor
    return GradientBoostingRegressor(n_estimators=100, max_depth=4, random_state=seed)


def _make_classifier(seed: int):
    backend = _gbm_backend()
    if backend == "lightgbm":
        return lgb.LGBMClassifier(n_estimators=200, max_depth=6, learning_rate=0.05,
                                  random_state=seed, verbose=-1)
    if backend == "xgboost":
        return xgb.XGBClassifier(n_estimators=200, max_depth=6, learning_rate=0.05,
                                 random_state=seed, verbosity=0, eval_metric="logloss")
    from sklearn.ensemble import GradientBoostingClassifier
    return GradientBoostingClassifier(n_estimators=100, max_depth=4, random_state=seed)


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Map market_data lowercase columns onto the live capitalized contract."""
    df = frame.rename(columns={k: v for k, v in COLUMN_ALIASES.items() if k in frame.columns})
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)


def build_context_momentum(frames: dict[str, pd.DataFrame], window: int = 30) -> pd.DataFrame | None:
    """Per-timestamp 30-bar momentum of the tracker ETFs, for merge_asof."""
    pieces = {}
    for sym, col in zip(CONTEXT_SYMBOLS, ("mom_2800", "mom_3033")):
        if sym not in frames:
            continue
        df = normalize_frame(frames[sym])
        close = pd.to_numeric(df["Close"], errors="coerce")
        mom = (close / close.shift(window) - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        pieces[col] = pd.DataFrame({"Date": df["Date"], col: mom})
    if not pieces:
        return None
    merged: pd.DataFrame | None = None
    for col, piece in pieces.items():
        merged = piece if merged is None else pd.merge_asof(
            merged.sort_values("Date"), piece.sort_values("Date"), on="Date",
        )
    return merged


def assemble_dataset(frames: dict[str, pd.DataFrame], config: TrainHKAlphaConfig) -> pd.DataFrame:
    """Features + labels for every tradable symbol, stacked and time-sorted."""
    context = build_context_momentum(frames)
    rows = []
    for symbol, raw in frames.items():
        if symbol in CONTEXT_SYMBOLS:
            continue
        df = normalize_frame(raw)
        if len(df) < config.max_bars + 5:
            continue
        feats = build_hk_alpha_features(df).frame
        session = df["Date"].dt.date
        labels = label_triple_barrier(
            df["Close"], config.tp, config.sl, config.max_bars,
            high=df.get("High"), low=df.get("Low"), session_id=session,
        )
        block = pd.concat([feats, labels], axis=1)
        block["symbol"] = symbol
        block["Date"] = df["Date"].values
        if context is not None:
            block = pd.merge_asof(
                block.sort_values("Date"), context.sort_values("Date"),
                on="Date", suffixes=("_drop", ""),
            )
            for col in ("mom_2800", "mom_3033"):
                drop = f"{col}_drop"
                if drop in block.columns:
                    block[col] = block[col].fillna(0.0)
                    block = block.drop(columns=[drop])
            block["flag_context"] = 1.0
        rows.append(block)
    if not rows:
        return pd.DataFrame()
    data = pd.concat(rows, ignore_index=True)
    data = data[data["valid"].astype(bool)].dropna(subset=["ret_h", "hit_tp_first"])
    return data.sort_values("Date").reset_index(drop=True)


def count_intraday_sessions(data: pd.DataFrame) -> int:
    """Distinct dates where at least one symbol has a real intraday session."""
    if data.empty:
        return 0
    per = data.groupby(["symbol", data["Date"].dt.date]).size()
    ok = per[per >= MIN_BARS_PER_SESSION]
    return ok.index.get_level_values(1).nunique() if len(ok) else 0


def _apply_symbol_te(train: pd.DataFrame, others: list[pd.DataFrame]) -> None:
    """Target-encode symbol with train-fold means only (no leakage)."""
    te = train.groupby("symbol")["ret_h"].mean()
    global_mean = float(train["ret_h"].mean()) if len(train) else 0.0
    for part in [train, *others]:
        part["symbol_te"] = part["symbol"].map(te).fillna(global_mean)


def walk_forward_train(data: pd.DataFrame, config: TrainHKAlphaConfig) -> tuple[dict, dict]:
    """Run purged walk-forward CV; return (models_bundle, metrics)."""
    n = len(data)
    chunk_edges = np.linspace(0, n, config.n_folds + 2, dtype=int)
    oos_parts: list[pd.DataFrame] = []

    for fold in range(1, config.n_folds + 1):
        train_end = chunk_edges[fold]
        val_start, val_end = chunk_edges[fold], chunk_edges[fold + 1]
        train = data.iloc[:train_end].copy()
        val = data.iloc[val_start:val_end].copy()
        # Embargo: drop the last embargo_bars rows of each symbol from train so
        # overlapping triple-barrier labels can't leak into the validation fold.
        reverse_rank = train.groupby("symbol").cumcount(ascending=False)
        train = train[reverse_rank >= config.embargo_bars]
        if len(train) < 100 or len(val) < 20:
            continue
        _apply_symbol_te(train, [val])

        reg = _make_regressor(config.seed)
        clf = _make_classifier(config.seed)
        reg.fit(train[FEATURE_ORDER], train["ret_h"])
        if train["hit_tp_first"].nunique() < 2:
            continue
        clf.fit(train[FEATURE_ORDER], train["hit_tp_first"].astype(int))

        part = val[["ret_h", "hit_tp_first"]].copy()
        part["pred_ret"] = reg.predict(val[FEATURE_ORDER])
        part["pred_prob"] = clf.predict_proba(val[FEATURE_ORDER])[:, 1]
        part["fold"] = fold
        oos_parts.append(part)

    if not oos_parts:
        return {}, {"error": "no_oos_folds"}

    oos = pd.concat(oos_parts, ignore_index=True)
    last_fold = int(oos["fold"].max())
    calib_fit = oos[oos["fold"] < last_fold]
    holdout = oos[oos["fold"] == last_fold]
    if len(calib_fit) < 50 or calib_fit["hit_tp_first"].nunique() < 2:
        calib_fit = oos  # tiny datasets: fall back to all OOS rows

    calibrator = IsotonicRegression(out_of_bounds="clip")
    calibrator.fit(calib_fit["pred_prob"], calib_fit["hit_tp_first"])

    nonzero = holdout[holdout["ret_h"] != 0.0]
    dir_acc = float((np.sign(nonzero["pred_ret"]) == np.sign(nonzero["ret_h"])).mean()) if len(nonzero) else 0.0
    try:
        auc = float(roc_auc_score(holdout["hit_tp_first"], holdout["pred_prob"]))
    except ValueError:
        auc = 0.5
    calibrated = np.clip(calibrator.predict(holdout["pred_prob"]), 0.0, 1.0)
    brier = float(brier_score_loss(holdout["hit_tp_first"], calibrated))

    metrics = {
        "backend": _gbm_backend(),
        "oos_rows": int(len(oos)),
        "holdout_rows": int(len(holdout)),
        "directional_accuracy": round(dir_acc, 4),
        "prob_auc": round(auc, 4),
        "brier_calibrated": round(brier, 4),
        "tp_base_rate": round(float(oos["hit_tp_first"].mean()), 4),
    }

    # Final fit on all rows for the production artifact.
    final = data.copy()
    _apply_symbol_te(final, [])
    final_reg = _make_regressor(config.seed)
    final_clf = _make_classifier(config.seed)
    final_reg.fit(final[FEATURE_ORDER], final["ret_h"])
    final_clf.fit(final[FEATURE_ORDER], final["hit_tp_first"].astype(int))
    symbol_te = final.groupby("symbol")["ret_h"].mean().to_dict()

    bundle = {
        "ret_head": final_reg,
        "prob_head": final_clf,
        "calibrator": calibrator,
        "feature_names": list(FEATURE_ORDER),
        "symbol_te": {k: float(v) for k, v in symbol_te.items()},
        "config": asdict(config),
        "backend": _gbm_backend(),
    }
    return bundle, metrics


def train_hk_alpha(
    frames: dict[str, pd.DataFrame],
    config: TrainHKAlphaConfig | None = None,
    *,
    dry_run: bool = False,
) -> TrainResult:
    cfg = config or TrainHKAlphaConfig()
    data = assemble_dataset(frames, cfg)

    sessions = count_intraday_sessions(data)
    if sessions < cfg.min_sessions:
        return TrainResult(
            status="blocked",
            reason=f"insufficient_sessions have={sessions} required={cfg.min_sessions}",
            metrics={"rows": int(len(data)), "sessions": sessions},
        )
    if len(data) < cfg.min_rows:
        return TrainResult(
            status="blocked",
            reason=f"insufficient_rows have={len(data)} required={cfg.min_rows}",
            metrics={"rows": int(len(data)), "sessions": sessions},
        )

    bundle, metrics = walk_forward_train(data, cfg)
    metrics["sessions"] = sessions
    metrics["rows"] = int(len(data))
    if not bundle:
        return TrainResult(status="blocked", reason="walk_forward_failed", metrics=metrics)

    guard_pass = (
        metrics["directional_accuracy"] >= cfg.min_dir_acc
        and metrics["prob_auc"] >= cfg.min_auc
        and metrics["brier_calibrated"] <= cfg.max_brier
    )
    metrics["guard_pass"] = guard_pass
    if not guard_pass:
        return TrainResult(
            status="blocked",
            reason=(
                "promotion_guard "
                f"dir_acc={metrics['directional_accuracy']} (min {cfg.min_dir_acc}) "
                f"auc={metrics['prob_auc']} (min {cfg.min_auc}) "
                f"brier={metrics['brier_calibrated']} (max {cfg.max_brier})"
            ),
            metrics=metrics,
        )
    if dry_run:
        return TrainResult(status="ok", reason="dry_run_no_artifact", metrics=metrics)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / f"{ARTIFACT_PREFIX}_{stamp}.pkl"
    sidecar = out_dir / f"{ARTIFACT_PREFIX}_{stamp}.json"
    with open(artifact, "wb") as fh:
        pickle.dump(bundle, fh)
    sidecar.write_text(json.dumps({
        "artifact": artifact.name,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "config": asdict(cfg),
        "guard_pass": True,
    }, indent=2))
    return TrainResult(status="ok", metrics=metrics,
                       artifact_path=str(artifact), sidecar_path=str(sidecar))


# ─── Data loading (CLI path) ──────────────────────────────────────────────────

def load_frames_from_db(db_path: str | Path = "kiro_quant.db") -> dict[str, pd.DataFrame]:
    """Load per-symbol HK frames from the market_data table."""
    db = Path(db_path)
    if not db.exists():
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM market_data WHERE symbol LIKE '%.HK' ORDER BY timestamp", conn,
        )
    finally:
        conn.close()
    if df.empty:
        return {}
    return {sym: g.drop(columns=["symbol"]).reset_index(drop=True) for sym, g in df.groupby("symbol")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train HKAlpha-1 prediction model V2")
    parser.add_argument("--dry-run", action="store_true", help="validate + report, write nothing")
    parser.add_argument("--sessions", type=int, default=60, help="minimum intraday sessions required")
    parser.add_argument("--db", default="kiro_quant.db", help="market data sqlite path")
    parser.add_argument("--output-dir", default="self_learn/models")
    args = parser.parse_args(argv)

    cfg = TrainHKAlphaConfig(min_sessions=args.sessions, output_dir=args.output_dir)
    frames = load_frames_from_db(args.db)
    result = train_hk_alpha(frames, cfg, dry_run=args.dry_run)

    if result.status == "blocked":
        print(f"status=blocked reason={result.reason}")
    else:
        print(f"status=ok metrics={json.dumps(result.metrics)}")
        if result.artifact_path:
            print(f"artifact={result.artifact_path}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
