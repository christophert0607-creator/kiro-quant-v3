# KiroQuant V3 — BACKLOG
> 較長期的規劃、功能增強、實驗性項目
> 唔影響生產運行，但值得記錄

---

## 🚀 有興趣但未開始

### B-001 | Kiro Quant × MiniMax-M2.7 深度整合
- **描述：** 利用 MiniMax-M2.7 做 complex reasoning 提升交易信號 quality
- **chat 顯示：** 2026-04-07 切換到 MiniMax-M2.7，但主要係 cron prompt 層面，未深入 strategy reasoning
- **想法：** 
  - 用 M2.7 做 pre-trade reasoning（解釋點解要進場）
  - 結合現有 RSI/MACD/VIX 信號做 multi-factor decision
  - Trail 呢個諗法一段時間睇效果
- **狀態:** 💭 Backlog

### B-002 | Walk-Forward Analysis (WFA) 自動化報告
- **描述：** 現有 WFA 需要手動跑 `trainer_v4_2_gpu.py`，建議自動化定期输出 HTML 報告
- **chat 顯示：** GPU training RTX 3060 已經 setup，但 decision trace 數據未主動做 WFA
- **想法：** 每日/每週自動跑 WFA → 寫入 `learning/wfa_reports/` → cron deliver
- **狀態:** 💭 Backlog

### B-003 | 通訊協議優化（Quant ↔ Dev A2A）
- **描述：** quant agent 同 dev agent 之間的 A2A 通訊可以結構化
- **chat 顯示：** 好多時靠手動 Telegram 協調
- **想法：** 定義 standard handoff packet format（JSON），減少歧義
- **狀態:** 💭 Backlog

### B-004 | V3 Dashboard（實時視覺化）
- **描述：** 現有 `dashboard/` 目錄，但唔知有冇真正用
- **chat 顯示：** office block dashboard v1.0 成功，但 quant dashboard 未見
- **想法：** Kiro Quant 即時 dashboard（持倉、pnl、signal strength）
- **狀態:** 💭 Backlog

---

## 🔬 實驗性

### B-005 | 情緒分析 → 市場情緒量化
- **描述：** `sentiment_score` 目前係`-1 to +1` 粗糙估算
- **想法：** 試用 VaderSentiment / TextBlob 對 news headlines 做更準確的 sentiment analysis
- **chat 顯示：** `_sync_sentiment()` 已存在，但只用 web search headlines
- **狀態:** 🔬 Experiment

### B-006 | 多市場同時監控
- **描述：** 目前 V3 只睇 HK/US，擴展到 EU/JP markets
- **想法：** 亞洲盤（JP、KR）、歐洲盤（DE、FR）作為宏觀指標
- **狀態:** 🔬 Experiment

### B-007 | 零知識驗證（ZKP）交易審計
- **描述：** 用 ZKP 確保 Decision Trace 不可篡改
- **想法：** 純粹係 long-term research idea，現有架構未需要
- **狀態:** 🔬 Experiment（遠期）

---

## 📝 文檔/研究

### B-008 | Kiro Quant Wiki 持續充實
- **描述：** Daily Research cron 已建立 Wiki ingest 流程
- **想法：** 建立 `wiki/` 結構（concepts/、entities/、sources/）
- **現況：** `wiki/` 目錄未見於 workspace-quant
- **狀態:** 📝 Research

### B-009 | 回測歷史整理
- **描述：** 現有 `backtest_harness.py` 但回測結果冇統一存放
- **想法：** 建立 `backtest_reports/YYYY-MM/` 結構，每次 WFA 結果自動存
- **狀態:** 📝 Research

### B-010 | 交易心理學模塊
- **描述：** 加入 `sell_signal_streak_by_symbol` 防震盪，但冇系統性交易心理框架
- **想法：** 根據 hold_time、drawdown、consecutive losses 調整 position size
- **chat 顯示：** 已有的 `sell_signal_streak` 機制係好嘅開始
- **狀態:** 📝 Research

---

## ⚠️ 技術債務

### B-011 | 清理 logs_archive/
- **描述：** `logs_archive/` 入面有大量 old log files
- **想法：** 確認壓縮/刪除舊 logs（保留最近 30 日）
- **狀態:** 🧹 Tech Debt

### B-012 | config.json 多版本清理
- **描述：** 見到 `config.json.bak_before_official`、`config.json.bak_v3test`
- **想法：** 合併備注、解釋每個版本的用途差異
- **狀態:** 🧹 Tech Debt

### B-013 | v3_launcher*.log 大量副本
- **描述：** 起碼 15+ 個 `v3_launcher_*.log` files
- **想法：** 合併到 `logs/` 子目錄，建立 log rotation
- **狀態:** 🧹 Tech Debt

### B-014 | self_learn 目錄結構優化
- **描述：** `self_learn/` 入面有 19+ 個 Python files（backfill、augment 等）
- **問題：** 未文檔化各 script 用途
- **想法：** 建立 `README.md` 說明每個 module 的角色
- **狀態:** 🧹 Tech Debt

---

## 📋 添加記錄

- **2026-04-17:** 初始化 Backlog（由 chat corpus 分析生成）
