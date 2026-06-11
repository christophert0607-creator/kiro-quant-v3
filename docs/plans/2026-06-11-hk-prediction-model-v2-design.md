# HK Prediction Model V2 (HKAlpha-1) Design

> **Implementation status (2026-06-11):** Tasks 3.1–3.6 全部落地,85 tests green。
> Shadow flags 已開(`hk_model_v2_enabled=true, mode=shadow` in both sections)。
> 現時 `kiro_quant.db` 只有 daily bars,`train_hk_alpha.py --dry-run` 如預期輸出
> `status=blocked reason=insufficient_sessions have=0 required=60` — 下一步係用
> idle scheduler backfill 1-min/5-min klines 累積 ≥60 sessions,先有 artifact 俾
> live loop load。冇 artifact 時 live loop log 一次 warning 後 fallback LSTM,零影響。

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.
> Builds on `2026-06-04-v3-prediction-upgrade-plan.md` (Phase 1–3 已落地: trade_quality, meta_label, provenance)。

**Goal:** 為 HK market 設計一套全新 prediction model,由「LSTM 預測下一個 price level」升級成「預測未來 N bars 嘅 forward return + calibrated 交易成功概率」,並針對 HK microstructure(lunch break、lot size、大陸聯動、開市 gap)建 feature。

**Architecture:** LightGBM return model + isotonic calibration head,以 champion–challenger 方式 shadow 現有 `v3_hk_stocks` LSTM,經 `report_prediction_health.py` 對比後先 promote。`ModelManager.predict()` 嘅 contract 保持不變(輸出 predicted price),所以 `main_loop` Phase 1 唔使改 signal 邏輯。

**Tech Stack:** Python, LightGBM (已有 import fallback in `manager.py`), pandas/numpy, sklearn isotonic calibration, SQLite self_learn DB, pytest。

---

## 0. 現有 HK Model 診斷

### 現況(2026-06-11 code 實證)

- HK model = `config.json → model.markets.HK = "v3_hk_stocks"`,即 `AttentiveKiroLSTM`(LSTM + self-attention),19 隻 HK symbols 共用一個 checkpoint。
- 訓練 target 係 **min-max scaled Close price level**(`DataPreparer.fit_transform`),唔係 return。
- Live inference 時每隻 symbol 用 `data_preparers_by_symbol` 喺 60-bar live buffer 上 re-fit min/max(`main_loop.py:752-769`)——scaler 隨 buffer 漂移。
- Confidence 係 heuristic:`0.6 × MAE-based + 0.4 × raw_ratio×50`(`main_loop.py:794-808`),唔係 calibrated probability。
- `outcome_head_enabled=false`(兩個 section 都係)——`outcome_prob` gate 有 config 位但無 model 餵佢。

### 核心問題

1. **預測 price level 係 near-identity task。** 下一個 bar 嘅 price ≈ 而家 price,MSE 最細嘅解係「照抄最後一個 Close」。模型學到嘅嘢同交易決策(會唔會升 ≥ threshold)幾乎無關。
2. **Per-symbol live re-fit scaler 引入 lookahead + 漂移。** min/max 用緊成個 buffer(包括最新 bar)計,股價創 buffer 新高/新低時 scale 突變,prediction 跟住跳。
3. **HK microstructure 完全無入 feature。** 冇 lunch break flag、冇開市 gap、冇 2800.HK/3033.HK 大市聯動(其實 `pulse_momentum.py` 已經計緊,只用嚟set posture)、冇 US overnight。
4. **Label 同 live exit economics 脫節。** Live 出場係 SL 2% / TP 2% / max_hold_bars 30,但 model 訓練同呢啲 barrier 無關。
5. **19 隻股共用一個 LSTM,但 inference 用各自漂移嘅 scaler** —— 等於每隻股睇到嘅 input 分佈都唔同。

### 設計原則(承襲 06-04 plan)

- 先 shadow,後 enforce;champion–challenger,唔即刻換模型。
- 所有 stationary feature,唔用 price level。
- Label 對齊 live exit barriers(triple-barrier)。
- Promotion 必須過 provenance guard(`test_meta_model_promotion_guard.py` 已有)。
- Kill switch 一個 config flag 還原。

---

## 1. Model Spec

### 1.1 Prediction target

**Triple-barrier forward outcome**,barrier 直接用 live config:

```text
upper barrier  = +quick_take_profit_pct  (+2%)
lower barrier  = −stop_loss_pct          (−2%)
vertical barrier = max_hold_bars         (30 bars @ 60s polling ≈ 30 min)
```

兩個 head:

| Head | Target | 用途 |
|------|--------|------|
| `ret_head` | 30-bar forward log return(clipped ±4%) | 換算 predicted price,維持 `predict()` contract |
| `prob_head` | P(先掂 upper barrier \| 唔掂 lower) ∈ {0,1} | calibrated 後做 `outcome_prob`,餵現有 outcome_head gate |

### 1.2 Model

- **Stage A:** 一個 LightGBM per head,全 HK symbols 共用一個 model + symbol target-encoding feature(19 隻股 data 太少,唔好 per-symbol model)。
- **Stage B:** `sklearn.isotonic.IsotonicRegression` calibration,用 walk-forward 最後一個 fold 嘅 out-of-sample prediction fit。
- LSTM **唔棄置**:`AttentiveKiroLSTM` 嘅 predicted move(`(lstm_pred − close)/close`)作為一個 input feature 餵入 LightGBM,等舊 model 變成 ensemble 成員。

### 1.3 Features(全部 stationary,~35 dim)

**Price/momentum(ATR-normalized):**
- log return 1/5/15/30 bars,各自除以 `ATR_14/close`
- `RSI_14`, `MACD_HIST/ATR_14`, `BB_POSITION`, `SMA_5/SMA_20 − 1`
- high-low range ratio(最新 bar 同 20-bar 平均比)

**Volume:**
- volume z-score(20-bar)
- turnover ratio(volume × close vs 20-bar 平均)

**HK market context(新增,核心 alpha 來源):**
- `2800.HK` 30m momentum、`3033.HK` 30m momentum(重用 `pulse_momentum.py` 計法,經 QuoteCache 攞)
- symbol 開市 gap:`open_today / prev_close − 1`
- US overnight:SPY 前一個 session return(`HistoricalDataDownloader` 已有 US daily)
- posture flag(`config.posture == "risk_on"` → 1.0)

**Session/time(HK 特有):**
- minutes since 09:30 open(normalized /390)
- `is_pre_lunch`(11:30–12:00)、`is_post_lunch_first30`(13:00–13:30)、`is_last_30min`(15:30–16:00)
- day-of-week one-hot(週一 gap / 週五 risk-off 效應)

**Symbol identity:**
- symbol target-encoded mean forward return(train fold 內計,防 leakage)
- LSTM predicted move(見 1.2)

### 1.4 Output contract

```python
@dataclass(frozen=True)
class HKPredictionV2:
    symbol: str
    expected_return: float      # ret_head output, 30-bar horizon
    predicted_price: float      # close * (1 + expected_return) — 兼容現有 main_loop
    prob_up: float              # calibrated P(hit TP before SL)
    confidence: float           # |2*prob_up - 1| — 取代 MAE heuristic
    horizon_bars: int           # 30
    model_id: str               # e.g. "hkalpha1_20260615"
    feature_flags: dict[str, bool]  # context features availability
```

---

## 2. Training Pipeline

### 2.1 Data

- **Primary:** Futu 1-min K-lines,經 idle scheduler backfill(`data/backfill_progress.json` 已 track),resample 到 60s bar 對齊 live polling。
- **Fallback/augment:** `v3_pipeline/data/storage/base_10y` daily parquet 計 daily-level context features(US overnight、gap)。
- 最少 60 個 HK sessions 先開 train;每 symbol 每 session ~330 bars → 19 symbols × 60 sessions ≈ 37 萬 rows。

### 2.2 Validation

- **Walk-forward purged CV:** 5 folds,每 fold embargo 30 bars(= vertical barrier,防 label overlap leakage)。
- 唔用 random split——time-series data random split 必然 leak。
- Metrics per fold:directional accuracy、`prob_head` AUC、calibration curve(Brier score)、simulated PnL(用 triple-barrier outcome 直接計)。

### 2.3 Promotion guard(重用現有)

Promote 做 live HK model 之前必須:

1. holdout directional accuracy ≥ 0.55(高過而家 self-learn report 嘅 0.57 唔強求,但要 beat LSTM baseline 喺同一 holdout 上)
2. `prob_head` AUC ≥ 0.55、Brier ≤ 0.25
3. shadow mode 跑滿 5 個 HK sessions,`report_prediction_health.py` 顯示 V2 directional accuracy ≥ LSTM
4. provenance guard pass(沿用 `test_meta_model_promotion_guard.py` 邏輯:synthetic-only 不可 promote enforce)

### 2.4 Retrain cadence

- **Nightly**(HK close 後 17:00 HKT):增量 retrain,跑 promotion guard,pass 先寫新 model file。
- **Weekend:** full retrain,掛入現有 `weekend_training_runner`(`tests/test_weekend_training_runner.py` 已有 harness)。
- Model artifacts: `self_learn/models/hkalpha1_YYYYMMDD_HHMMSS.pkl` + sidecar JSON(metrics、feature list、train window)——跟現有 `meta_*.json` / `model_*.pkl` convention。

---

## 3. Implementation Tasks

### Task 3.1: Feature builder

**Files:**
- Create: `v3_pipeline/models/hk_alpha_features.py`
- Test: `tests/test_hk_alpha_features.py`

`build_hk_alpha_features(frame, context) -> pd.DataFrame`。Pure function,input 係 featured OHLCV frame(`TechnicalIndicatorGenerator` output)+ context dict(2800/3033 momentum、US overnight、posture),output 固定欄位順序嘅 feature frame。Missing context → 填 0.0 + flag column(同 `trade_outcome_features.py` 嘅 `source_flags` 風格一致)。

**Test cases:** 欄位順序 deterministic;NaN/Inf 清洗;context 缺失時 flag=0;lunch break bar 嘅 session feature 正確。

### Task 3.2: Triple-barrier labeler

**Files:**
- Create: `self_learn/triple_barrier.py`
- Test: `tests/test_triple_barrier.py`

`label_triple_barrier(close: pd.Series, tp: float, sl: float, max_bars: int) -> pd.DataFrame`,輸出 `ret_30`, `hit_tp_first`, `bars_to_exit`。Barriers 由 config 讀(`stop_loss_pct`, `quick_take_profit_pct`, `max_hold_bars`),唔好 hardcode。

**Test cases:** 先掂 TP;先掂 SL;timeout;序尾不足 30 bars 嘅 rows drop;TP/SL 同一 bar 掂(用 conservative 假設:算 SL)。

### Task 3.3: Trainer + calibration

**Files:**
- Create: `self_learn/scripts/train_hk_alpha.py`
- Test: `tests/test_hk_alpha_trainer.py`(用 synthetic data 細跑)

Walk-forward CV、isotonic calibration、promotion guard、artifact 寫入。CLI:

```bash
PYTHONPATH=. python3 self_learn/scripts/train_hk_alpha.py --dry-run
PYTHONPATH=. python3 self_learn/scripts/train_hk_alpha.py --sessions 60
```

`--dry-run` 喺 data 不足時輸出 `status=blocked reason=insufficient_sessions have=N required=60`(跟 06-04 plan 嘅 blocked 風格)。

### Task 3.4: Live predictor wrapper

**Files:**
- Create: `v3_pipeline/models/hk_predictor_v2.py`
- Test: `tests/test_hk_predictor_v2.py`

`HKPredictorV2.predict(wfa_frame, context) -> HKPredictionV2`。Load 最新 pass-guard artifact(行為跟 `ModelRegistry.resolve` 一致,加 alias `v3_hk_stocks_v2` 入 `models_registry.json`)。無 artifact → raise,caller fallback LSTM。

### Task 3.5: main_loop shadow integration

**Files:**
- Modify: `v3_pipeline/core/main_loop.py`(prediction section,`model_predict` emit 之後)
- Modify: `config.json`(`v3_live` + `hk_live` 兩邊都要寫,避免 HK overlay bypass)
- Test: `tests/test_hk_model_v2_shadow.py`

**Config shape:**

```json
{
  "hk_live": {
    "hk_model_v2_enabled": true,
    "hk_model_v2_mode": "shadow"
  }
}
```

**Shadow 行為:** market=HK 時計 V2 prediction,emit structured event,**唔影響**現有 prediction/confidence/signal:

```text
[HK_MODEL_V2][0700.HK] mode=shadow pred_v2=315.2 ret=0.0041 prob_up=0.58 conf=0.16 lstm_pred=314.8 model=hkalpha1_20260615
```

同時寫入 self_learn `predictions`(`model_version_id` 指向 V2 artifact),等 shadow 期都累積 prediction_error 數據。

**Enforce 行為(Stage C 先開):** `prediction = v2.predicted_price`、`confidence = v2.confidence`,LSTM 退役做 feature;`outcome_head_enabled=true` + `outcome_prob = v2.prob_up` 餵現有 gate(`outcome_prob_min` HK=0.60 已 config 好)。

### Task 3.6: Health report split

**Files:**
- Modify: `scripts/report_prediction_health.py`

加 `--model v2|lstm|both` 對比:directional accuracy、MAE、prob calibration(分 bucket 嘅 realized win rate vs predicted prob)。Shadow 期每日跑,做 promotion 證據。

---

## 4. Rollout Plan

### Stage A — Shadow(≥5 HK sessions)
```text
hk_model_v2_enabled=true, hk_model_v2_mode=shadow
outcome_head_enabled=false (unchanged)
```
成功標準:無 crash、`HK_MODEL_V2` events 齊、V2 predictions 入 DB、health report 出到對比數。

### Stage B — Promotion review
跑 `report_prediction_health.py --model both --days 5`。V2 directional accuracy ≥ LSTM 且 calibration buckets 單調 → 入 Stage C;否則留 shadow,調 feature/重訓。

### Stage C — Enforce prediction source
```text
hk_model_v2_mode=enforce
```
只換 prediction/confidence 來源,所有 gate(trade_quality / meta_label)行為不變。觀察 2 sessions。

### Stage D — Outcome head 接通
```text
outcome_head_enabled=true, outcome_head_mode=shadow → enforce
```
`prob_up` 作為 `outcome_prob`,行 06-04 plan Phase 4 嘅 enforce rule。

### Kill switch
```json
{ "hk_live": { "hk_model_v2_enabled": false }, "v3_live": { "hk_model_v2_enabled": false } }
```
還原後 runtime 必須停 emit `HK_MODEL_V2`,prediction 路徑同今日完全一樣。

---

## 5. Test Bundle

```bash
cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3
PYTHONPATH=. pytest \
  tests/test_hk_alpha_features.py \
  tests/test_triple_barrier.py \
  tests/test_hk_alpha_trainer.py \
  tests/test_hk_predictor_v2.py \
  tests/test_hk_model_v2_shadow.py \
  tests/test_trade_quality_filter.py \
  tests/test_meta_label_gate.py \
  -q

python3 -m py_compile \
  v3_pipeline/models/hk_alpha_features.py \
  v3_pipeline/models/hk_predictor_v2.py \
  self_learn/triple_barrier.py \
  self_learn/scripts/train_hk_alpha.py \
  v3_pipeline/core/main_loop.py
```

---

## 6. Risks / Open Questions

1. **1-min kline backfill 深度:** Futu API 對 1-min 歷史有限制(一般 ~2 年內,且按 quota)。若 60 sessions 攞唔齊,先用 5-min bar 起步(vertical barrier 改 6 bars = 30 min,economics 不變)。
2. **Lunch break bar 處理:** 12:00–13:00 無 bar。Forward barrier 計 bars 時要 session-aware,唔可以跨 lunch 當連續——labeler 必須用 session index 而非 wall-clock。
3. **19 symbols 流動性差異大**(0700 vs 2688):turnover ratio feature + trade_quality gate 應該已 cover,但 shadow 期要睇 per-symbol calibration 有冇 systematically 偏。
4. **執行順序:** 跟 06-04 plan 嘅教訓——**唔好未有 data 就上 model**。Task 3.1–3.3 可以即刻做(testable offline),Task 3.5 enforce 必須等 shadow 證據。
