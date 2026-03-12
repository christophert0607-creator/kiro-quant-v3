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

## Issue #16-19 交易系統擴展（2026-03）
- 訂單成交確認：`FutuConnector.get_order_status(order_id)` + `wait_for_fill()`，在更新本地倉位前先確認成交。
- Paper Trading：新增 `PaperTradingSimulator`，模擬成交滑點、現金與持倉，輸出 `paper_trading_pnl.json`。
- 自動重連：新增 `reconnect(max_retries=10)`，採指數退避（5s, 10s, 20s ...）並在重連後同步倉位與訂單。
- P&L 追蹤：新增 `PnLTracker`，記錄成本價、成交時間、已實現/未實現盈虧，輸出 `pnl_report.json`。
