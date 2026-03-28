# Meta-Labeling Pipeline

Meta-labeling ML system for KiroQuant V3 — learns which model signals to allow/block.

## Quick Start

```bash
# Run full pipeline
python3 dataset_extractor.py --join
python3 dataset_extractor.py --enrich --joined out/joined_events.jsonl --out out/enriched_events.jsonl
python3 label_generator.py
python3 baseline_model.py

# Continuous learning
python3 continuous_learning.py --keep-previous
```

## Pipeline Stages

| Stage | Script | Purpose |
|-------|--------|---------|
| M0 | `dataset_extractor.py` | Extract decisions + account snapshots, join, enrich with OHLCV/indicators |
| M1 | `label_generator.py` | Label events: forward return > threshold → 1 (winner) else 0 |
| M2 | `baseline_model.py` | Train LogisticRegression baseline, report metrics |
| M3 | `backtest_harness.py` | Threshold sweep to find optimal probability cutoff |
| M4 | `inference.py` | Live scoring of decisions |
| M6 | `export_joblib.py` | Export model to joblib for v3_pipeline/ml/meta_gate.py |
| M7 | `continuous_learning.py` | Full pipeline orchestration with model archival |
| M8 | `performance_monitor.py` | Track allow/block decisions in production |
| M9 | `rollout_validation.py` | Full component validation |

## Output Files

- `out/enriched_events.jsonl` — feature-rich events with OHLCV + indicators
- `out/labeled_events.jsonl` — labeled outcomes (binary)
- `out/model_weights.json` — trained model coefficients + scaler
- `out/model_metrics.json` — accuracy, precision, recall, AUC, Brier score
- `out/backtest_results.json` — threshold sweep results
- `out/meta_decisions.jsonl` — live production decisions
- `models/lineage.jsonl` — model version audit trail

## Current Model (2026-03-27)

- 68 events (34 labeled: 5 positive / 29 negative)
- Threshold 0.6+ → 100% win rate (backtest, 4 trades)
- Features: confidence, snapshot_*, ohlcv_*, ind_* (20 indicators)
