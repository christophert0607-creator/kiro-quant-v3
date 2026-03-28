# Meta-labeling (P0) — Implementation Plan

**Goal**: Add a lightweight meta-labeling layer to filter existing entry signals and improve win-rate / reduce churn.

## Data sources (already in place)
- `learning/us_sim/account_snap_us_sim.jsonl`
- `learning/us_sim/decision_trace_us_sim.jsonl`

## Decisions needed (pending)
- Label horizon: 30m vs 1D vs both
- Objective: win-rate vs expectancy vs drawdown

## Milestones

### M0 — Data plumbing (read-only)
1. Build a dataset extractor that joins:
   - decision trace events (BUY_PLACED, BUY_BLOCKED_*, SELL_PLACED)
   - account snapshots (closest previous snapshot)
2. Add OHLCV + indicators features for symbols at decision time (via yfinance 1m/5m).
3. Persist dataset to `dev/meta_labeling/datasets/us_sim_YYYY-MM-DD.parquet`.

### M1 — Labeling
1. Compute forward return labels (configurable horizons).
2. Build classification target:
   - `y=1` if fwd_return > 0 (or > threshold), else 0.
3. Store label metadata and split rules.

### M2 — Baseline model
1. Train baseline: LogisticRegression + standard scaling.
2. Compare vs XGBoost (optional) once dataset > 500 samples.
3. Metrics: accuracy, precision/recall, AUC, calibration.

### M3 — Backtest harness (vectorbt later)
1. Simple replay: apply meta-model probability threshold to BUY decisions.
2. Compare “before vs after”: trade count, win rate, max DD proxy.

### Guardrails
- No changes to live trading execution logic unless explicitly approved.
- Each incremental change must pass `py_compile`.
- All changes logged in `dev/meta_labeling/DEVLOG.md`.
