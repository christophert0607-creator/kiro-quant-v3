# Kiro Quant V3 Codex 任務清單 (根據《量化交易》書籍優化)

## 🎯 當前任務

### 🔴 任務 5: Futu Connection Failover [優先]
**問題**: V3.5 一直嘗試連接 Futu，需要 failover

**需要修復**:
- [ ] 失敗 3 次後自動切換到 Infoway/yfinance
- [ ] 添加 connection failure counter
- [ ] 確保 DataManager 被正確調用

---

## 📋 《量化交易》書籍優化建議 (2026-03-14)

### 🟡 新任務 12: Kelly Formula 資金管理 [最高優先]

**根據 Ernest Chan 書籍建議**:
- 實施 Kelly Formula 計算最佳倉位大小
- 部分 Kelly (50%) 以降低波動
- 根據波動性動態調整倉位

**公式**: f* = (bp - q) / b
- f* = 最佳投資比例
- b = 淨賠率
- p = 贏的概率
- q = 輸的概率 = 1 - p

**修改檔案**:
- v3_pipeline/risk/manager.py
- config.py

---

### 🟡 新任務 13: 交易成本模型 [高優先]

**根據書籍第三章建議**:
- 加入真實佣金計算 (約 0.1%-0.2%)
- 加入滑點模型 (0.05%-0.1%)
- 計算交易成本對策略的影響

**修改檔案**:
- v3_pipeline/execution/cost_calculator.py
- main_loop.py

---

### 🟡 新任務 14: 回測陷阱防範 [高優先]

**根據書籍第三章建議**:
- [ ] 防止前瞻偏差 (look-ahead bias) - 使用確切歷史時間點
- [ ] 防止數據窺探偏差 (data-snooping) - 樣本外測試
- [ ] 正確計算夏普比率 (Sharpe Ratio)
- [ ] 加入生存者偏差檢查

**修改檔案**:
- flash_backtest.py
- deep_backtest.py

---

### 🟡 新任務 15: 進階風險度量 [高優先]

**根據書籍第六章建議**:
- 最大回撤 (Maximum Drawdown) 追蹤
- VaR/CVaR 計算
- 壓力測試 (歷史情境模擬)
- 風險調整後報酬計算

**修改檔案**:
- v3_pipeline/risk/manager.py

---

### 🟢 新任務 16: 策略評估指標 [中優先]

**根據書籍第二章建議**:
- 夏普比率 (Sharpe Ratio)
- 資訊比率 (Information Ratio)
- 卡爾瑪比率 (Calmar Ratio)
- 最大回撤持續時間

**修改檔案**:
- v3_pipeline/analysis/metrics.py

---

### 🔵 新任務 17: 策略類型擴展 [中優先]

**根據書籍第七章建議**:
- [ ] 均值回歸策略測試
- [ ] 動量策略優化
- [ ] 市場狀態識別 (牛市/熊市/震盪)
- [ ] 配對交易測試

**修改檔案**:
- v3_pipeline/strategies/

---

### 🟣 新任務 18: 心理與執行紀律 [中優先]

**根據書籍第六章建議**:
- 自動化交易執行 (減少人為干預)
- 交易日記系統
- 情緒化交易警告

**修改檔案**:
- main_loop.py
- 新增交易日記模組

---

## 📋 研究報告建議 (2026-03-14)

### 🟡 新任務 8: 資料驗證與品質 [高優先]

### 🟡 新任務 8: 資料驗證與品質 [高優先]

**根據報告建議**:
- 建立穩定 ETL 管道
- 加入資料校驗 (schema 檢查 / 時區統一 / 數值上下限)
- 設置資料品質監控指標 (缺失率 / 異常值率)

**修改檔案**:
- data_manager.py
- v3_pipeline/data/

---

### 🟡 新任務 9: 自動化風控 [高優先]

**根據報告建議**:
- 實施動態止損/止盈
- 設置策略最大單日虧損限額
- VaR/CVaR 計算
- 資金分配模型

**修改檔案**:
- v3_pipeline/risk/manager.py
- v3_pipeline/core/main_loop.py

---

### 🟢 新任務 10: 監控與觀察度 [中優先]

**根據報告建議**:
- 部署監控系統 (Prometheus/Grafana)
- 收集關鍵指標
- 設置異常告警

**修改檔案**:
- 新增監控腳本

---

### 🔵 新任務 11: 回測與仿真優化

**根據報告建議**:
- 加入真實交易成本模型 (佣金/滑點)
- 優化回測速度
- 對比回測 vs 實盤差異

**修改檔案**:
- v3_pipeline/backtest/engine.py

---

## ✅ 已完成 (封存)

| 任務 | 狀態 | PR |
|------|------|-----|
| ROR_GATE 調整 | ✅ | #45 |
| 止盈/止損 | ✅ | #45 |
| 分散持倉 | ✅ | #45 |
| Infoway 整合 | ✅ | #46 |
| DataManager 強化 | ✅ | #49, #50 |
| 自動備份 | ✅ | Cron |

---

## 📊 數據位置
- 持倉備份: /home/tsukii0607/.openclaw/workspace/kiro_quant_daily/paper_trading/
- 日誌: v3_live_*.log
