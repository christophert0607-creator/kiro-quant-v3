# V3 Prediction Upgrade Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 將 V3 由「簡單 price prediction 開倉」升級成「prediction + trade-quality + meta-label + regime-aware + provenance-backed feedback」的交易決策系統，優先降低 turnover / 弱訊號 / 集中倉風險。

**Architecture:** 保留現有 LSTM `ModelManager.predict()` 作為 baseline alpha source，不即時大換模型。先在 `LiveTradingLoop` prediction 後、下單前插入 `TradeQualityFilter` 與 `MetaLabelGate`，所有 accept/reject 都寫入 structured logs / decisions.jsonl；再逐步接入 CL embedding + XGBoost/LightGBM head，並分 HK/US calibration。

**Tech Stack:** Python, pandas/numpy, PyTorch LSTM, SQLite self_learn DB, pytest, existing V3 modules: `v3_pipeline/core/main_loop.py`, `v3_pipeline/models/manager.py`, `self_learn/*`, `config.json`.

---

## 0. Current Diagnosis

### 現況
- Core prediction: `ModelManager.predict()` 輸出 predicted price。
- Live path: `prediction/current_price` 轉成 `model_buy/model_sell`。
- Confidence: MAE / directional accuracy / raw predicted move 混合 heuristic。
- Current self-learn report:
  - holdout accuracy: `0.60`
  - win_rate: `0.50`
  - directional_acc: `0.57`
  - outcomes: `100`
  - source: `synthetic_seed` only
- CL encoder / meta-label artifacts 存在，但 live pre-order filter 未完全成為強制主閘。

### 核心問題
1. Prediction 太像「價格方向估計」，不是「交易成功概率」。
2. Confidence 不是 calibrated probability。
3. 高 turnover + 大單倉時，簡單 signal 會放大虧損。
4. Synthetic-only self-learn outcome 不可用作加倉依據。
5. HK / US market microstructure 未完全分開。

### 升級原則
- 保守優先：先減少爛交易，再追求更高命中率。
- 先 filter，後改 model。
- 所有新 gate 先 shadow mode，再 enforce mode。
- 每個 gate 必須有 structured log，方便復盤。
- 任何 model promotion 必須通過 real/paper broker provenance guard。

---

## Phase 1 — Trade Quality Filter（最高優先）

**目的:** 在不動 LSTM 主模型下，新增交易品質分數，阻止弱 signal / 高 turnover signal / 過度集中 signal。

**新增概念:**

```text
prediction
  → predicted_move
  → confidence
  → trade_quality_score
  → quality_decision: ACCEPT / REJECT / SHADOW_ONLY
  → existing risk gates
```

### Task 1.1: 建立 TradeQualityFilter 模組

**Objective:** 新增獨立、可單元測試的 quality scorer。

**Files:**
- Create: `v3_pipeline/core/trade_quality.py`
- Test: `tests/test_trade_quality_filter.py`

**Data inputs:**
- `symbol`
- `market`: HK / US
- `prediction`
- `current_price`
- `confidence`
- `latest_ind`: RSI, MACD_HIST, ATR, BB_POSITION if available
- `position_context`: current qty, notional, per-position cap remaining
- `recent_stats`: recent win rate / prediction error / turnover penalty if available

**Score components:**

```python
score = (
    0.30 * confidence_score
    + 0.20 * move_strength_score
    + 0.15 * technical_alignment_score
    + 0.15 * recent_symbol_health_score
    + 0.10 * volatility_sanity_score
    + 0.10 * concentration_safety_score
    - turnover_penalty
)
```

**Decision thresholds:**
- `score >= 0.65`: ACCEPT
- `0.50 <= score < 0.65`: SHADOW_ACCEPT only if conservative mode off
- `< 0.50`: REJECT

**Expected log:**

```text
[TRADE_QUALITY][0700.HK] decision=REJECT score=0.43 conf=0.52 move=0.006 atr=... reason=low_score,concentration
```

**Test cases:**
1. High confidence + strong move + no concentration → ACCEPT.
2. Low confidence → REJECT.
3. Position already over cap → REJECT.
4. High turnover penalty → REJECT.
5. HK lot-size impossible after cap → REJECT.

**Verification:**

```bash
cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3
PYTHONPATH=. pytest tests/test_trade_quality_filter.py -q
python3 -m py_compile v3_pipeline/core/trade_quality.py
```

---

### Task 1.2: 加 config flags

**Objective:** 令 quality filter 可 shadow / enforce / rollback。

**Files:**
- Modify: `config.json`
- Modify: `v3_launcher.py` if LiveConfig fields are constructed there
- Modify: `v3_pipeline/core/main_loop.py` LiveConfig dataclass if applicable

**Config shape:**

```json
{
  "v3_live": {
    "trade_quality_enabled": true,
    "trade_quality_mode": "shadow",
    "trade_quality_min_score": 0.65,
    "trade_quality_shadow_min_score": 0.50,
    "trade_quality_turnover_penalty": 0.15
  },
  "hk_live": {
    "trade_quality_enabled": true,
    "trade_quality_mode": "shadow",
    "trade_quality_min_score": 0.68,
    "trade_quality_shadow_min_score": 0.52,
    "trade_quality_turnover_penalty": 0.20
  }
}
```

**Important:** Must write both `v3_live` and `hk_live` to avoid HK overlay bypass.

**Verification:**

```bash
python3 -m json.tool config.json >/dev/null
python3 - <<'PY'
import json
cfg=json.load(open('config.json'))
for sec in ['v3_live','hk_live']:
    print(sec, cfg.get(sec, {}).get('trade_quality_enabled'), cfg.get(sec, {}).get('trade_quality_mode'))
PY
```

---

### Task 1.3: Insert shadow scoring after prediction

**Objective:** 在 `LiveTradingLoop._process_symbol` prediction 後 emit quality result，但 shadow mode 不阻單。

**Files:**
- Modify: `v3_pipeline/core/main_loop.py` around prediction section after structured `model_predict`
- Test: `tests/test_main_loop_trade_bridge.py` or new focused test

**Insertion point:** after current code emits:

```python
self._emit_structured("model_predict", ...)
```

**Behavior:**
- Always calculate quality if enabled.
- Write structured event:

```python
self._emit_structured(
    "trade_quality",
    symbol=symbol,
    score=round(score, 4),
    decision=decision,
    reasons=reasons,
    mode=mode,
)
```

**Shadow mode:** no order blocking yet.

**Verification:**

```bash
PYTHONPATH=. pytest tests/test_main_loop_trade_bridge.py tests/test_trade_quality_filter.py -q
python3 -m py_compile v3_pipeline/core/main_loop.py
```

Runtime smoke after restart:

```bash
grep 'trade_quality\|TRADE_QUALITY' logs/v3_live.log | tail -20
```

---

### Task 1.4: Enforce quality filter only on BUY / SHORT entry

**Objective:** SELL / risk exit 不可被 quality filter 阻擋；只阻新開倉。

**Files:**
- Modify: `v3_pipeline/core/main_loop.py`
- Test: `tests/test_live_execution_ordering.py`

**Rules:**
- BUY entry: block if `decision=REJECT` and mode=`enforce`.
- SHORT entry: block if `decision=REJECT` and mode=`enforce`.
- SELL exit / stop loss / risk reduction: never block.
- Portfolio replacement SELL: never block.

**Expected logs:**

```text
[TRADE_QUALITY_BLOCK][AAPL] action=BUY score=0.42 reasons=low_confidence,turnover_penalty
```

**Verification:**

```bash
PYTHONPATH=. pytest tests/test_live_execution_ordering.py tests/test_trade_quality_filter.py -q
python3 -m py_compile v3_pipeline/core/main_loop.py
```

---

## Phase 2 — Meta-label Gate as Pre-order Filter

**目的:** 令 prediction 不再直接決定交易；prediction 先經 meta-label 判定「值唔值得交易」。

### Task 2.1: Rebuild / locate MetaLabelGate

**Objective:** 若現有 `self_learn/meta_labeler.py` 不在 live tree，重建最小穩定版。

**Files:**
- Create or modify: `self_learn/meta_labeler.py`
- Test: `tests/test_meta_label_gate.py`

**Interface:**

```python
from enum import Enum

class MetaDecision(str, Enum):
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"
    REVERSE = "REVERSE"
    NO_DATA = "NO_DATA"

class MetaLabelGate:
    def should_take_trade(self, symbol, action, entry_price, predicted_price, confidence, indicators=None):
        ...
```

**Fallback behavior:**
- No enough real/paper outcomes → `NO_DATA`
- Synthetic-only evidence → `NO_DATA`, not CONFIRM
- High confidence override disabled initially

**Verification:**

```bash
PYTHONPATH=. pytest tests/test_meta_label_gate.py -q
python3 -m py_compile self_learn/meta_labeler.py
```

---

### Task 2.2: Add meta-label config

**Files:**
- Modify: `config.json`
- Modify: LiveConfig construction path

**Config:**

```json
{
  "v3_live": {
    "meta_label_enabled": true,
    "meta_label_mode": "shadow",
    "meta_label_min_real_outcomes": 100,
    "meta_label_allow_no_data": false,
    "meta_label_allow_reverse": false
  },
  "hk_live": {
    "meta_label_enabled": true,
    "meta_label_mode": "shadow",
    "meta_label_min_real_outcomes": 100,
    "meta_label_allow_no_data": false,
    "meta_label_allow_reverse": false
  }
}
```

**Why shadow first:** Current outcomes are synthetic_seed only; immediate enforcement may block all entries.

---

### Task 2.3: Insert meta-label shadow event before order queue

**Objective:** prediction → quality → meta-label → existing gates.

**Files:**
- Modify: `v3_pipeline/core/main_loop.py`
- Test: `tests/test_main_loop_trade_bridge.py`

**Structured event:**

```text
[META_LABEL][MSFT] decision=NO_DATA action=BUY source_ok=false eligible_outcomes=0 mode=shadow
```

**Enforce rules:**
- `CONFIRM`: allow
- `REJECT`: block new entry
- `REVERSE`: do not reverse initially; log only
- `NO_DATA`: shadow mode allow; enforce mode block or tiny-size mode only after explicit choice

**Verification:**

```bash
PYTHONPATH=. pytest tests/test_main_loop_trade_bridge.py tests/test_meta_label_gate.py -q
python3 -m py_compile v3_pipeline/core/main_loop.py self_learn/meta_labeler.py
```

---

## Phase 3 — Provenance-backed Feedback Loop

**目的:** 停止用 synthetic_seed 當 live learning truth；每個 outcome 要分清楚 paper/live broker evidence。

### Task 3.1: Confirm outcome provenance schema

**Files:**
- Inspect/modify: `self_learn/models.py`
- Inspect/modify: `self_learn/schema.py`
- DB migration script if needed: `self_learn/scripts/apply_outcome_provenance_schema.py`

**Required columns in outcomes:**
- `source`
- `recorded_by`
- `broker_order_id`
- `provenance_meta`

**Rules:**
- `synthetic_seed` excluded from promotion.
- `paper_broker` with broker/order evidence eligible.
- `live_broker` with broker/order evidence eligible.

**Verification SQL:**

```bash
sqlite3 self_learn/trading_bot.db "PRAGMA table_info(outcomes);"
sqlite3 self_learn/trading_bot.db "SELECT source, recorded_by, COUNT(*) FROM outcomes GROUP BY source, recorded_by;"
```

---

### Task 3.2: Wire close-out outcome recording

**Objective:** `on_trade_closed()` must record provenance whenever broker/paper close completes.

**Files:**
- Modify: `self_learn/feedback.py`
- Modify: close call sites in `v3_pipeline/core/main_loop.py`
- Possibly modify: execution layer return object if broker order id currently lost

**Expected outcome record:**

```json
{
  "source": "paper_broker",
  "recorded_by": "v3_live_loop",
  "broker_order_id": "...",
  "provenance_meta": {"market":"HK", "trd_env":"SIMULATE", "close_reason":"TAKE_PROFIT"}
}
```

**Verification:**
- Unit test `on_trade_closed()` writes source fields.
- Integration smoke with fake broker result.
- No silent `try/except Exception: pass` swallowing signature mismatch.

---

### Task 3.3: Promotion guard

**Objective:** Retrain may train in memory, but must not persist/promote model unless provenance gate passes.

**Files:**
- Modify: `self_learn/retrain.py`
- Test: `tests/test_meta_model_promotion_guard.py`

**Gate:**
- Feature shape valid
- finite matrix
- >= 100 eligible real/paper broker outcomes
- symbol coverage minimum
- holdout accuracy >= 0.60
- no synthetic-only promotion

**Verification:**

```bash
PYTHONPATH=. pytest tests/test_meta_model_promotion_guard.py -q
```

Expected if only synthetic outcomes:

```text
status=blocked reason=meta_model_promotion_guard eligible_real_source_count=0
```

---

## Phase 4 — CL Embedding + XGBoost/LightGBM Head

**目的:** 由「預測價格」升級成「預測 trade outcome probability」。

### Task 4.1: Modify LSTM to optionally return hidden state

**Files:**
- Modify: `v3_pipeline/models/manager.py`
- Test: `tests/test_model_manager_hidden_state.py`

**Change:**

```python
class AttentiveKiroLSTM(nn.Module):
    def forward(self, x, return_hidden: bool = False):
        seq, _ = self.lstm(x)
        attn_out, _ = self.attn(seq, seq, seq, need_weights=False)
        fused = self.norm(seq + attn_out)
        last = fused[:, -1, :]
        out = self.fc(self.dropout(last))
        if return_hidden:
            return out, last
        return out
```

**Backward compatibility:** existing `predict()` must still work.

**Verification:**

```bash
PYTHONPATH=. pytest tests/test_model_manager_hidden_state.py tests/test_model_registry.py -q
python3 -m py_compile v3_pipeline/models/manager.py
```

---

### Task 4.2: Create feature builder for meta head

**Files:**
- Create: `v3_pipeline/models/trade_outcome_features.py`
- Test: `tests/test_trade_outcome_features.py`

**Feature vector:**
- LSTM hidden: 96 dim if current hidden_dim=96
- CL embedding: 128 dim
- Technical/risk features: 10-20 dim
- Market one-hot: HK/US
- Position/concentration features

**Output:**

```python
TradeOutcomeFeatures(
    x=np.ndarray,
    feature_names=list[str],
    source_flags={"cl_available": bool, "hidden_available": bool}
)
```

---

### Task 4.3: Train outcome probability head

**Files:**
- Create: `self_learn/scripts/train_trade_outcome_head.py`
- Store model: `self_learn/models/trade_outcome_head_YYYYMMDD.pkl`

**Labels:**
- Primary: profitable outcome yes/no
- Secondary: pnl_pct bucket if enough data

**Important:** no promotion until eligible real/paper provenance count >= 100.

**Verification:**

```bash
PYTHONPATH=. python3 self_learn/scripts/train_trade_outcome_head.py --dry-run
```

Expected until enough real/paper data:

```text
status=blocked eligible_real_source_count=0 required=100
```

---

### Task 4.4: Shadow inference in live loop

**Objective:** Compute `outcome_prob` but do not block initially.

**Files:**
- Modify: `v3_pipeline/core/main_loop.py`

**Log:**

```text
[OUTCOME_HEAD][NVDA] prob_profit=0.61 ev=0.008 mode=shadow source=head_v1
```

**Future enforce rule:** only allow new BUY if:

```text
trade_quality_score >= threshold
AND meta_label != REJECT
AND outcome_prob >= 0.58
```

---

## Phase 5 — HK / US Split Calibration

**目的:** HK 同 US 分開 threshold、turnover penalty、holding period、position cap sensitivity。

### Task 5.1: Market-specific config contract

**Files:**
- Modify: `config.json`
- Modify: config loader / `v3_launcher.py`

**Settings:**

```json
{
  "v3_live": {
    "prediction_threshold_default": 0.004,
    "trade_quality_min_score": 0.65,
    "outcome_prob_min": 0.58
  },
  "hk_live": {
    "prediction_threshold_default": 0.006,
    "trade_quality_min_score": 0.68,
    "outcome_prob_min": 0.60,
    "turnover_penalty_multiplier": 1.25
  }
}
```

**Reason:** HK lot size + liquidity + lunch break + policy beta require stricter entry.

---

### Task 5.2: Market-specific reporting

**Files:**
- Create: `scripts/report_prediction_health.py`

**Report fields:**
- HK / US split:
  - predictions count
  - accepted / rejected by quality
  - meta decisions
  - outcome head avg probability
  - orders attempted
  - realized pnl if available
  - turnover

**Command:**

```bash
PYTHONPATH=. python3 scripts/report_prediction_health.py --days 1
```

---

## Phase 6 — Runtime Rollout Plan

### Stage A: Shadow mode, no behavior change

**Config:**

```text
trade_quality_mode=shadow
meta_label_mode=shadow
outcome_head_mode=shadow
```

**Duration:** 1 HK session + 1 US session.

**Success criteria:**
- No crash.
- New events visible:
  - `trade_quality`
  - `META_LABEL`
  - optional `OUTCOME_HEAD`
- Reject ratio measurable.
- No order path blocked.

---

### Stage B: Enforce TradeQuality only

**Config:**

```text
trade_quality_mode=enforce
meta_label_mode=shadow
outcome_head_mode=shadow
```

**Success criteria:**
- Turnover reduced by >= 30% vs baseline.
- No full-day no-trade caused by overblocking unless market truly weak.
- Conservative mode remains active:
  - `per_position_cap_fraction=0.05`
  - `max_orders_per_cycle=1`
  - HK/US throttle active

---

### Stage C: Enforce MetaLabel only after real/paper evidence

**Condition:**
- eligible paper/live outcomes >= 100
- provenance schema ready
- source filter confirmed

**Config:**

```text
trade_quality_mode=enforce
meta_label_mode=enforce
meta_label_allow_no_data=false
```

---

### Stage D: Outcome head shadow → enforce

**Condition:**
- outcome head holdout stable
- no synthetic-only promotion
- market split metrics acceptable

---

## Kill Switch / Rollback

All new logic must be disabled with config only:

```json
{
  "v3_live": {
    "trade_quality_enabled": false,
    "meta_label_enabled": false,
    "outcome_head_enabled": false
  },
  "hk_live": {
    "trade_quality_enabled": false,
    "meta_label_enabled": false,
    "outcome_head_enabled": false
  }
}
```

Rollback verification:

```bash
python3 -m json.tool config.json >/dev/null
pkill -f "v3_launcher" 2>/dev/null || true
NO_FUTU_QUOTE=1 python3 v3_launcher.py > logs/dashboard-v3-launcher.out.log 2>&1
```

Runtime logs must stop emitting new gate events if disabled.

---

## Required Test Bundle Before Restart

```bash
cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3
PYTHONPATH=. pytest \
  tests/test_trade_quality_filter.py \
  tests/test_meta_label_gate.py \
  tests/test_main_loop_trade_bridge.py \
  tests/test_live_execution_ordering.py \
  tests/test_futu_connector_unlock.py \
  -q

python3 -m py_compile \
  v3_pipeline/core/main_loop.py \
  v3_pipeline/core/trade_quality.py \
  v3_pipeline/models/manager.py \
  self_learn/meta_labeler.py \
  self_learn/feedback.py \
  self_learn/retrain.py \
  v3_launcher.py
```

---

## Restart / Health Verification

```bash
cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3

# 1. confirm only one launcher before restart
ps aux | grep v3_launcher | grep -v grep

# 2. restart launcher only; keep healthy FutuOpenD
pkill -f "v3_launcher" 2>/dev/null || true
sleep 2
NO_FUTU_QUOTE=1 python3 v3_launcher.py > logs/dashboard-v3-launcher.out.log 2>&1 &
sleep 10

# 3. verify
ps aux | grep v3_launcher | grep -v grep
tail -30 logs/v3_live.log
curl -s --max-time 3 -o /dev/null -w "3000:%{http_code}\n" http://localhost:3000/kiro
curl -s --max-time 3 -o /dev/null -w "8787:%{http_code}\n" http://localhost:8787/health
curl -s --max-time 3 -o /dev/null -w "18888:%{http_code}\n" http://localhost:18888/health
```

Expected:
- one launcher PID
- fresh `v3_live.log`
- dashboard endpoints 200
- no `Traceback|CRASH|ECONNREFUSED`
- `trade_quality` logs in shadow mode

---

## Acceptance Criteria

### Short-term success, 1-2 sessions
- New trade quality logs present.
- Enforce mode can reduce weak BUYs without blocking exits.
- Turnover drops materially.
- No double-engine, no crash, no config corruption.

### Medium-term success, 1-2 weeks
- Broker/paper provenance outcomes accumulate.
- Meta-label no longer relies on synthetic_seed.
- Rejected signals can be reviewed against realized outcome.
- HK and US metrics reported separately.

### Long-term success
- Outcome probability head becomes primary pre-order filter.
- Prediction no longer equals trade decision.
- Model promotion requires real/paper broker evidence.
- System learns from actual trading quality, not seeded synthetic outcomes.

---

## Recommended Execution Order

1. Phase 1 Task 1.1-1.3: TradeQuality shadow mode.
2. Run one live/paper session and inspect logs.
3. Phase 1 Task 1.4: Enforce TradeQuality only.
4. Phase 3: Provenance loop, because meta-label needs real/paper truth.
5. Phase 2: Meta-label shadow → enforce after evidence.
6. Phase 4: CL + outcome head shadow.
7. Phase 5: HK/US split calibration.

**Do not start with Phase 4.** Model complexity before quality/provenance will amplify false confidence.
