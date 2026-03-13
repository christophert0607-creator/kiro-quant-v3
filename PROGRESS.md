# 🔧 系統重整進度 (Kiro Quant V3)
**更新時間**: 2026-03-10

## 🎯 本輪目標
將系統重新整理為更輕便可切換的執行架構，並完成一次可驗證的重新編譯檢查。

## ✅ TASK LIST 進度
- [x] 盤點現有入口與配置（`v3_launcher.py`, `config.example.json`）
- [x] 建立「輕便 / 標準」雙執行配置（runtime profile）
- [x] 增加 CLI 參數覆寫能力（`--profile`, `--config`, `--dry-run`）
- [x] 更新文件，加入新架構與使用方式
- [x] 執行重新編譯檢查（`python -m compileall`）
- [x] 執行 launcher dry-run 驗證

## 🧱 架構調整摘要
1. **Runtime Profile 機制**
   - `lite`：預設低負載（較少 lookback / 較小 hidden_dim / 單層網路）
   - `standard`：保持原本較高容量配置
2. **配置來源統一**
   - 新增 `_load_cfg()` 集中讀取 JSON 設定
3. **啟動模式強化**
   - 加入 `--dry-run` 快速檢查啟動參數，不進入交易主循環

## 🧪 驗證結果
- 編譯檢查：通過
- dry-run：通過（可正確輸出 profile 與 symbols）

## 📌 下一步建議
- 加入 `micro` profile（超低資源巡檢模式）
- 針對 `lite` profile 建立基準延遲與記憶體監控報表
- 將 profile 選擇接入 dashboard 以便運維切換


## 🆕 2026-03-12 交易解鎖補強（PR follow-up）
- [x] 新增 `FUTU_TRADE_PASSWORD` REAL 交易解鎖流程補強與錯誤提示
- [x] 修正 `get_acc_list` 在部分 SDK 版本不支援 `trd_env` 參數時的回退邏輯
- [x] 補齊交易連接器單元測試（`tests/test_futu_connector_unlock.py`）
- [x] 執行 `python -m pytest tests/` 全部通過
- [x] 執行模擬盤（Paper Trading）連線驗證日誌：`unlock_calls=0`

## 🆕 2026-03-13 Issue #35 交易橋接修復（CRITICAL）
- [x] 明確建立 `check_and_trade()` 橋接函數，將預測到交易決策路徑顯性化（`_run_symbol_cycle -> check_and_trade -> _run_trading_logic`）
- [x] 新增交易決策可觀測日誌：`TRADE_CHECK` / `PROFILE_GATE` / `HOLD`，避免「有預測但無交易日誌」盲區
- [x] 新增回歸測試（`tests/test_main_loop_trade_bridge.py`）覆蓋：交易橋接調用與 `allow_long=False` 攔截記錄
- [x] 執行完整測試：`python -m pytest tests/` 全部通過
- [x] 執行 Paper Trading 驗證日誌：`PAPER_ORDER TSLA BUY qty=81 ...`（確認交易路徑可觸發）
- [x] 執行簡易 profiling（5,000 次 `check_and_trade`）確認新增日誌邏輯未造成異常性回歸
