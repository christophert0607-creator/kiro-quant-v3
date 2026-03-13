# Codex 任務清單 (Daily Task)

## 任務目標
優化 Kiro Quant 交易系統，解決以下問題：

---

## ✅ 完成度總覽（更新）
- **已完成代碼層修改**：優先任務 1、2、3
- **待實盤/模擬驗證**：任務 4（交易頻率）與部分驗收指標
- **最新狀態**：已可從本文件直接追蹤「已完成」與「待驗證」

---

## 🔴 優先任務 1: 降低 ROR_GATE 阻擋率

### 問題
- 現狀: ror=1.0, threshold=0.350
- 阻擋了 90% 的交易

### 期望
- 調整閾值至 0.1-0.2
- 目標：通過率 > 70%

### 已完成內容
- [x] 預設 `ruin_threshold` 已調整到 `0.15`
- [x] ROR gate 判斷改為 survival score (`1 - ror`) 與 threshold 比較
- [x] 補充對應單元測試

### 修改文件
- `v3_pipeline/risk/manager.py`
- `tests/test_risk_manager.py`

---

## 🔴 優先任務 2: 確保止盈/止損觸發

### 問題
- 系統從未觸發止盈/止損益賣出

### 期望
- 1% 止盈觸發
- 2% 止損觸發

### 已完成內容
- [x] 保留 1% quick take-profit (`quick_take_profit_pct=0.01`)
- [x] 新增 2% stop-loss (`stop_loss_pct=0.02`)
- [x] 補充 stop-loss 交易邏輯測試

### 修改文件
- `v3_pipeline/core/main_loop.py` (sell logic)
- `tests/test_main_loop_trade_bridge.py`

---

## 🟡 任務 3: 分散持倉

### 問題
- 資金集中在 1 隻股票

### 期望
- 持倉分散到 3-5 隻股票
- 每隻股票不超過總資金 30%

### 已完成內容
- [x] 新增 `max_positions`（預設 5）
- [x] 新增 `max_position_fraction_per_symbol`（預設 0.30）
- [x] 套用於 swing 與 model entry
- [x] 配置擴展至 `config.json` / `v3_launcher.py`

### 修改文件
- `v3_pipeline/core/main_loop.py`
- `v3_launcher.py`
- `config.json`
- `tests/test_main_loop_trade_bridge.py`

---

## 🟢 任務 4: 優化交易頻率

### 期望
- 每天 5-10 次交易
- 快速輪轉 (有賺就賣)

### 目前狀態
- [x] 已加入「快速輪轉」相關邏輯（quick take-profit、max hold bars）
- [ ] **待用模擬/實盤日誌驗證**是否達到「>5 次/日」

---

## 執行步驟

1. [x] **分析代碼**: 閱讀 `main_loop.py`, `risk controller` 實作
2. [x] **識別問題**: 找出阻塞交易的原因
3. [x] **修改代碼**: 調整參數和邏輯
4. [x] **創建 PR**: 提交修改
5. [ ] **測試驗證**: 需補充一段可量化的模擬交易統計（按天交易次數）

---

## 驗收標準

- [ ] ROR_GATE 阻擋率 < 30%（待以最新交易日誌驗證）
- [x] 止盈/止損益能觸發（已由單元測試覆蓋）
- [x] 持倉分散至 3-5 隻（已由持倉上限與單標的上限控制）
- [ ] 交易頻率 > 5 次/日（待模擬/實盤統計）

---

## 數據位置
- 持倉: `paper_trading_pnl.json`
- 日誌: `v3_live_*.log`
- 配置: `config.json`
