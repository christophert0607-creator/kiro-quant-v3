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


## Issue #29 + #38/#39 交易邏輯更新（2026-03-13）
- 日誌可觀測性：在主循環輸出 `[{SYMBOL}] Prediction (inverse, current, change%)`，避免多標的時無法識別來源。
- 波段策略：交易決策加入 RSI / MACD 交叉 / 支撐阻力觸發條件（可由 `LiveConfig` 開關與參數調整）。
- 風險邊界：波段進場沿用 confidence 風險倉位；波段出場為全平倉以降低反轉風險。
- 回歸測試：`tests/test_main_loop_trade_bridge.py` 新增波段買賣訊號與 symbol 預測日誌測試。


## Issue #35/#29 診斷策略補充（2026-03-13）
- 交易診斷：使用 `DIAG_GATE` 檢查 `allow_long`、`qty`、swing/model 訊號與 `bypass_ror_gate` 狀態。
- 臨時繞過：`LiveConfig.bypass_ror_gate=True` 可在定位階段跳過 ROR gate（預設 `False`）。
- 門檻判斷：模型路徑以 `predicted_move`（相對變化）對比 `prediction_threshold`。
- 防呆：若倉位為負，記錄 `DIAG_QTY` 並 clamp 為 0，避免後續決策污染。

- 診斷回歸：針對 `DIAG_QTY` 與 ROR bypass warning 需有單元測試覆蓋，避免診斷開關失效。

## Issue #41 快速止盈與持倉時限（2026-03-13）
- 快速止盈：`LiveConfig.quick_take_profit_pct` 預設 `1%`，當現價達入場價 +1% 時優先全平倉。
- 持倉時限：`LiveConfig.max_hold_bars` 預設 `5`，持倉超過上限 K 線後強制平倉，避免長時間 hold 侵蝕手續費。
- 風險邊界：止盈與時限退出均加註 `RISK BOUNDARY` 註釋，明確其風險控制目的。
- 測試覆蓋：`tests/test_main_loop_trade_bridge.py` 新增 1% 止盈與 5 根 K 線強制退出回歸測試。


## Issue #42 多任務 Pattern Recognition（2026-03-13）
- 新增 `StockPatternModel`（CNN-LSTM + temporal attention）雙頭輸出：價格變化回歸 + pattern 分類。
- 新增 `trainer_pattern_v1.py`：支援 parquet sliding window、rule-based pseudo label、`0.7*MSE + 0.3*CE` 多任務 loss。
- 主循環新增 `predict_pattern()` 輸出：`Detected Pattern: <label>, Prob=<p>`，並於高信心 bullish pattern 下放寬入場閾值。
- Dashboard 新增 Pattern Heatmap（symbol x pattern 平均 confidence）與最新 pattern 訊號列表。
- 回歸要求：執行 `python -m pytest tests/`，並跑 `check_and_trade` profiling 確認性能可接受。

## Issue #42 follow-up（2026-03-13）
- 修復向後兼容：`LiveTradingLoop` 對舊版 `ModelManager`（無 `predict_pattern`）改為安全 fallback，避免部署時因介面落差中斷。
- 修復可用性：pattern snapshot CSV 寫入加入例外保護，確保主迴圈穩定。
- 修復訓練邊界：`trainer_pattern_v1` 小樣本 split 風險防護。
- 回歸覆蓋：新增無 `predict_pattern` 相容測試，並再次跑全量 pytest + paper log + profiling。

## Issue #43 comment follow-up（2026-03-13）
- 強化 `predict_pattern` payload 驗證：非 dict 輸出不再觸發屬性錯誤，改記錄 warning 並 fallback。
- 補齊回歸測試：無 `predict_pattern` 與 malformed payload 均可穩定執行 `_run_symbol_cycle`。
- 再次驗證：pytest / paper trading log / profiling 全部重跑。
