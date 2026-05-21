# Meta_012 — Decision Matrix & Trigger Conditions

## Status
- **Task**: meta_012 — 實現決策邏輯：何時覆蓋 base signal
- **Blocker**: 需要 ≥100 closed trade outcomes 才能驗證 live 触发
- **Core Engine**: `self_learn/meta_labeler.py` — 完成，通過單元+整合測試

---

## Decision Matrix

### Threshold Constants (from meta_labeler.py)
| Constant | Value | Description |
|----------|-------|-------------|
| `DIR_ACC_CONFIRM_THRESHOLD` | 0.55 | Minimum directional accuracy to CONFIRM |
| `DIR_ACC_REVERSE_THRESHOLD` | 0.40 | Maximum directional accuracy before REVERSE |
| `MAE_CONFIRM_THRESHOLD` | 5.0 | Maximum MAE (price units) to CONFIRM with low MAE bonus |
| `CONFIDENCE_OVERRIDE_THRESHOLD` | 0.80 | Model confidence that can override directional accuracy |

---

## Decision Logic Flow

```
Base signal arrives: {symbol, action=BUY/SELL, entry_price, predicted_price, confidence}
                              │
                              ▼
                 ┌──────────────────────────────┐
                 │ Compute symbol_accuracy via   │
                 │ get_prediction_accuracy()    │
                 │ Returns: {mae, dir_acc}      │
                 └──────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        dir_acc == 0.5   dir_acc > 0.5   dir_acc < 0.5
        (no history)    (bullish bias)  (bearish bias)
              │               │               │
              ▼               │               │
         NO_DATA             │               │
    (skip safely)            │               │
                    ┌────────┴────────┐        │
                    ▼                ▼        │
               conf ≥ 0.80    conf < 0.80     │
                    │                │        │
                    ▼                ▼        │
               CONFIRM           dir_acc ≥ 0.55
               (override)              │
                                        ▼
                                   dir_acc ≤ 0.40
                                        │
                                        ▼
                                   REVERSE
                                   (override)

    Middle zone (0.40 < dir_acc < 0.55):
      → REJECT (not confident enough to act)
```

---

## Decision Outcomes

| Decision | When Triggered | Action on Base Signal |
|----------|-----------------|----------------------|
| **CONFIRM** | dir_acc ≥ 0.55 OR (conf ≥ 0.80 AND dir_acc ≥ 0.55) | Take the trade as-is |
| **REJECT** | 0.40 < dir_acc < 0.55 | Skip the trade, do nothing |
| **REVERSE** | dir_acc ≤ 0.40 | Do the OPPOSITE of base signal |
| **NO_DATA** | dir_acc = 0.5 (insufficient history) OR symbol_accuracy = None | Skip safely, log for monitoring |

---

## Override Behavior

### Confidence Override (conf ≥ 0.80)
When model confidence is very high, it can override the directional accuracy requirement:
- **CONFIRM override**: conf ≥ 0.80 AND dir_acc ≥ 0.55
- Example: conf=0.85, dir_acc=0.57 → CONFIRM (reason mentions "overrides")

This means: even if historical directional accuracy is moderate, a high-confidence prediction from the model can still be taken.

### Reversal Confidence Scaling
When REVERSE is triggered, confidence is computed as `(0.5 - dir_acc)`:
- dir_acc=0.35 → confidence=0.15 (weak reversal)
- dir_acc=0.20 → confidence=0.30 (stronger reversal)
- dir_acc=0.00 → confidence=0.50 (maximum reversal confidence)

---

## Per-Symbol Accuracy Computation

`compute_symbol_accuracy(symbol, window=20)` calls `get_prediction_accuracy(symbol, window=20)`:

```python
# get_prediction_accuracy from models.py:
# - mae: mean absolute error between predicted_price and exit_price
# - dir_acc: fraction of trades where (exit_price - entry_price) has same sign as (predicted_price - entry_price)
```

**Window=20 means**: Look at the 20 most recent closed trades for this symbol to compute accuracy metrics.

---

## Meta_012 Trigger Conditions (for live integration)

When we have ≥100 closed outcomes, the following conditions must ALL be met for meta_labeler to actively override:

1. **Base signal must have a linked prediction**: signal.prediction_id must reference a valid Prediction
2. **Prediction must have confidence and predicted_price**: non-null values
3. **Symbol must have closed trade history**: get_prediction_accuracy returns non-default values
4. **Decision must not be NO_DATA**: if NO_DATA, simply skip (safe fallback)

### Live Integration Hook Points

In `main_loop.py`, before executing a BUY/SELL signal:
```python
from self_learn.meta_labeler import should_take_trade, Decision

# Get linked prediction
pred = get_prediction(prediction_id)

# Evaluate with meta_labeler
decision = should_take_trade(
    symbol=symbol,
    action=action,  # BUY or SELL
    entry_price=entry_price,
    predicted_price=pred.predicted_price,
    confidence=pred.confidence,
)

if decision.decision == Decision.REJECT:
    skip_trade(reason=decision.reason)
elif decision.decision == Decision.REVERSE:
    execute_opposite(action=flip_action(action), reason=decision.reason)
elif decision.decision == Decision.CONFIRM:
    execute_as_planned(reason=decision.reason)
elif decision.decision == Decision.NO_DATA:
    # Fallback: use original signal (meta_labeler cannot evaluate)
    execute_as_planned(reason="NO_DATA - proceeding with base signal")
```

---

## Backtesting Before Live

Before enabling live override, must run backtest simulation:

1. **Offline simulation**: Replay historical signals through meta_labeler
2. **Compare outcomes**: With vs without meta-labeling on same historical period
3. **Minimum backtest period**: 3 months of signals with closed trades

**Backtest metric**: `meta_label_rejection_rate = rejected_signals / total_signals`

Expected range: 10-30% rejection rate (depends on symbol accuracy distribution)

---

## Blocker Resolution Path

| Step | Condition | Action |
|------|-----------|--------|
| 1 | outcomes < 100 | Monitor, wait for live trades to close |
| 2 | outcomes ≥ 20 | Run backtest simulation script (meta_020 prep) |
| 3 | outcomes ≥ 100 | Run `check_training_readiness.py` → if ready, proceed to meta_012 live integration |
| 4 | backtest shows improvement | User approval for live override enablement |

---

## Files

| File | Purpose |
|------|---------|
| `self_learn/meta_labeler.py` | Decision engine (COMPLETE, tested) |
| `self_learn/scripts/check_training_readiness.py` | Readiness monitor (COMPLETE) |
| `self_learn/scripts/evaluate_open_signals.py` | Open signal batch audit (COMPLETE) |
| `tests/test_meta_labeler.py` | Unit tests 10/10 passing |
| `tests/test_meta_labeler_integration.py` | Integration tests 5/5 passing |

---

## Verification

```bash
# Run all meta_labeler tests
cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3
PYTHONPATH=. pytest tests/test_meta_labeler.py tests/test_meta_labeler_integration.py -v

# Run readiness check (will show blocker)
PYTHONPATH=. python3 self_learn/scripts/check_training_readiness.py

# Run meta quality report
PYTHONPATH=. python3 self_learn/scripts/meta_quality_report.py
```

---

## Next Action After Blocker Clears

When `check_training_readiness.py` returns `ready=True`:
1. Run backtest simulation on historical signals (meta_020)
2. If backtest shows improvement, document in DEVLOG
3. Propose user approval for live override enablement in `main_loop.py`