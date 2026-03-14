---
name: futu-api
description: |
  Kiro Quant Futu + V3 pipeline operation skill.
  Use this when an agent needs to:
  - run/rebuild the V3 launcher and switch runtime profile (lite/standard)
  - execute end-to-end health checks (compile, dry-run, core commands)
  - update task progress documents and handoff notes
  - operate Futu OpenAPI data/trading workflow with fallback data sources
---

# Futu API + Kiro V3 操作技能

## 何時使用
當使用者要求以下任一項目時，啟用本技能：
1. 「重整 / 重編譯 / 輕量化」V3 流程
2. 要 Agent 自行接手執行整套量化流程
3. 更新任務進度（TASK LIST / PROGRESS）
4. Futu API 行情、資金流、持倉、模擬交易排查
5. 匯報 Kiro Quant 系統狀態

## 最小執行流程（Agent Runbook）
1. **讀配置**：先讀 `config.json`（無則參考 `config.example.json`）
2. **選 profile**：`v3_live.runtime_profile`（預設 `lite`）
3. **做快檢**：`python v3_launcher.py --dry-run`
4. **做編譯檢查**：`python -m compileall v3_launcher.py v3_pipeline`
5. **需要時才實跑**：`python v3_launcher.py --profile lite|standard`
6. **更新進度文件**：按完成項目更新 `PROGRESS.md`

> 詳細流程與命令清單見：`references/v3-agent-workflow.md`

## 快速命令
```bash
# 1) 啟動前快檢
python v3_launcher.py --dry-run
python v3_launcher.py --dry-run --profile standard

# 2) 編譯檢查
python -m compileall v3_launcher.py v3_pipeline

# 3) V3 執行
python v3_launcher.py --profile lite

# 4) Futu 常用查詢
python futu_api.py quote TSLA --market US
python futu_api.py capital TSLA --market US
python futu_api.py assets --env simulated
```

## 文件更新規則
- 架構改動：同步更新 `v3_pipeline/README.md`
- 配置改動：同步更新 `config.example.json`
- 執行狀態：同步更新 `PROGRESS.md`
- 交付時需附：已執行命令、結果、後續建議
- REAL 交易模式：需設定 `FUTU_TRADE_PASSWORD`（兼容 `FUTU_TRADE_PWD`），並在連線後完成 `unlock_trade` 驗證
- 交易代碼調整：至少新增/更新對應單元測試，並執行 `python -m pytest tests/`

---

# 2026-03-13 系統更新日誌

## ✅ 已完成

### DataManager 品質監控（2026-03-14）
- 新增市場數據 payload 驗證流程（price/source/timestamp/volume）
- 新增來源級品質指標（total/invalid/missing 與 rate）
- 當 invalid-rate 超過門檻時輸出 warning log

### PR #40 - Swing 信號 + 診斷日誌
- 添加 RSI/MACD Swing 交易信號
- 添加診斷日誌 (DIAG_GATE, DIAG_QTY)
- 添加 ROR bypass 控制

### PR #42 - 快速止盈/止損
- 添加 1% 快速止盈
- 添加 max_hold_bars 持倉上限

### PR #44 - 多任務形態識別 + Bug 修復
- 添加 Pattern Trainer
- 修復 Issue #43：重複買入同一隻股票
- 添加持倉檢查：`qty == 0` 先可以買入

### 系統功能
1. **Telegram 自動彙報** - 每30分鐘
2. **模擬倉重置** - 每天 16:00，按實時現價平倉
3. **交易分析** - 記錄買入時間、成本、現價

---

## ⚠️ 待優化問題
- ROR_GATE 太嚴格
- 無賣出觸發
- 分散投資

---

## 📊 當前系統狀態
- V3.5: 運行中
- 模擬倉: $100,000
- 持倉: 0


## 🆕 2026-03-14 風險門檻擴充指引（Daily Loss + VaR/CVaR）
- `RiskConfig` 可配置：
  - `max_daily_loss_fraction`：日內最大可承受虧損比例（預設 `1.0`，不影響既有行為）。
  - `max_trade_cvar_95`：單筆交易 CVaR(95) 下限（預設 `None`，不啟用）。
- `RiskController` 新增：
  - `allow_daily_loss(day_start_equity, current_equity)`
  - `estimate_cvar_95(return_samples|tail_losses_95)`
  - `allow_trade_with_var_cvar(mc_var_95, ...)`
- `LiveTradingLoop` 實務：
  - 每日換日重設 `day_start_equity`。
  - 每次交易週期先執行日內虧損檢查。
  - BUY 前再執行一次 gate，阻擋時輸出 `[DAILY_LOSS_GATE]` / `[VAR_CVAR_GATE]` 明確日誌。
