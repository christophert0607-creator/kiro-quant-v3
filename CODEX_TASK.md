# Codex 任務清單 (Research Report 更新版)

## 🎯 當前任務

### 🔴 任務 5: Futu Connection Failover [優先]
**問題**: V3.5 一直嘗試連接 Futu，需要 failover

**需要修復**:
- [ ] 失敗 3 次後自動切換到 Infoway/yfinance
- [ ] 添加 connection failure counter
- [ ] 確保 DataManager 被正確調用

---

## 📋 研究報告建議 (2026-03-14)

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
