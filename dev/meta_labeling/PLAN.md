# Meta-Labeling Plan

## 目標
建立 Meta-Labeling 系統：利用機器學習模型預測誤差（MAE/directional accuracy）來二次確認或否决 base strategy 產生的交易信號，減少假信號。

## 現況 (as of 2026-05-19)
- **DB:** `trading_bot.db` (SQLAlchemy) — 10606 predictions, 0 signals, 0 outcomes
- **Schema Issue:** `prediction_error` column 已修復（models.py vs schema.py 不一致）
- **信號缺失:** LiveTradingLoop 似乎沒有成功寫入 signals 表

## Task List

### Phase 1: 修復數據流問題
- [ ] **meta_001** ✅ 修復 schema sync (`prediction_error` column)
- [ ] **meta_002** 確認 LiveTradingLoop 的 `hook_on_signal` 為何 signals=0
  - 檢查 `v3_pipeline/core/main_loop.py` 中信號記錄邏輯
  - 確認信號是否被靜默捕獲（exception pass）
- [ ] **meta_003** 確認 `on_trade_closed` hook 是否正常寫入 outcomes

### Phase 2: Meta-Labeling 核心邏輯
- [ ] **meta_010** 設計 meta-labeling 架構
  - Input: base strategy signal + feature vector + prediction
  - Output: confirm / reject / reverse
- [ ] **meta_011** 實現 `meta_labeler.py` — 訓練 meta-label 模型
  - 使用歷史 prediction_error 和實際 outcomes
  - 特徵：RSI, MACD, volume, regime, prediction confidence
- [ ] **meta_012** 實現決策邏輯：何時覆蓋 base signal

### Phase 3: 回測驗證
- [ ] **meta_020** 在 backtest engine 中模擬 meta-labeling 效果
- [ ] **meta_021** 對比有/無 meta-labeling 的策略表現

### Phase 4: 實盤整合
- [ ] **meta_030** 將 meta-labeling 整合進 LiveTradingLoop
- [ ] **meta_031** 設置監控 dashboard