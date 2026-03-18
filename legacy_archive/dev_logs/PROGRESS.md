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

## 🆕 2026-03-13 Issue 狀態同步（#32 / #28 / #25 / #24 / #23）
- [x] 同步多實例模擬架構（#32）狀態為「🟡 諮詢中」
- [x] 同步 Codex 執行任務（#28）狀態為「🟡 進行中」
- [x] 同步 GPU 長期訓練（#25）狀態為「🟡 進行中」
- [x] 同步 安全隱患 + Dashboard（#24）狀態為「🟡 部分完成」
- [x] 同步 pyarrow 依賴（#23）狀態為「🟡 已修復」


## 🆕 2026-03-13 Issue #29 + #38/#39 波段策略與日誌修復
- [x] 修復主循環預測日誌缺少股票代碼：新增 `[{SYMBOL}] Prediction (inverse/current/change%)`
- [x] 交易邏輯升級為波段訊號優先：RSI 超買/超賣、MACD 金叉/死叉、支撐/阻力觸發
- [x] 保留原預測閾值策略作為 fallback，維持向後兼容
- [x] 新增單元測試覆蓋波段買入、波段賣出、symbol 預測日誌
- [x] 執行 `python -m pytest tests/` 全部通過
- [x] 執行 Paper Trading 驗證：可產生 `PAPER_ORDER` 日誌
- [x] 執行 `check_and_trade` profiling（5,000 次）確認新增判斷邏輯可接受


## 🆕 2026-03-13 Issue #35/#29 交易診斷與門檻修復（follow-up）
- [x] 新增 `DIAG_GATE` 診斷日誌，顯示 swing/model 訊號與 `allow_long` / `qty` / ROR bypass 狀態
- [x] 新增 `bypass_ror_gate` 開關（預設關閉）供問題定位時臨時繞過 ROR gate
- [x] 修正模型進出場觸發判斷為「相對變化」(`predicted_move` 對 `prediction_threshold`)
- [x] 強化參數防護：負倉位 qty 記錄 `DIAG_QTY` 並自動 clamp 至 0
- [x] 補齊單元測試：relative threshold、ROR bypass、allow_long/qty 診斷上下文
- [x] 執行 `python -m pytest tests/` 全部通過
- [x] 執行 Paper Trading 驗證：`PAPER_ORDER` 日誌可觸發
- [x] 執行 `check_and_trade` profiling（5,000 次）

- [x] 回應 review comment：新增 `DIAG_QTY` / `bypass_ror_gate` 日誌測試，確保診斷行為可驗證

## 🆕 2026-03-13 Issue #41 快速止盈（1%）+ 持倉上限（5 根 K）
- [x] 新增 `LiveConfig.quick_take_profit_pct=0.01`，有賺頭即走（達入場價 +1% 優先觸發 SELL）
- [x] 新增 `LiveConfig.max_hold_bars=5`，持倉超過 5 根 K 線強制平倉，避免過度持有
- [x] 交易主路徑加入風險邊界註釋（`RISK BOUNDARY`）說明止盈/時限平倉目的
- [x] 補齊單元測試：`tests/test_main_loop_trade_bridge.py` 新增 quick take profit 與 max hold bars 退出場景
- [x] 執行 `python -m pytest tests/` 全部通過
- [x] 執行 Paper Trading 驗證：`PAPER_ORDER ... type=NORMAL` 日誌可觸發
- [x] 執行 `cProfile`（5,000 次 `check_and_trade`）確認新增判斷可接受


## 🆕 2026-03-13 Multi-task Pattern Recognition 升級（CNN-LSTM + Temporal Attention）
- [x] 新增 `StockPatternModel`（CNN-LSTM + Attention）支援雙頭輸出：價格回歸 + 型態分類
- [x] 新增 `trainer_pattern_v1.py`：從 `base_10y` parquet 建立 sliding window，採 `0.7*MSE + 0.3*CE` 訓練
- [x] 新增 rule-based pattern 自動標註邏輯（HeadShoulderTop/DoubleBottom/UpTrend/Reversal/VolumePulse/Triangle）
- [x] 主循環整合 pattern 推論：輸出 `Detected Pattern` log，並用高信心 bullish pattern 放寬入場閾值
- [x] dashboard 新增 Pattern Heatmap 區塊，顯示 symbol x pattern 平均信心與最新訊號
- [x] 補齊單元測試：pattern model/dataset + 交易閾值 pattern gate
- [x] 執行 `python -m pytest tests/` 全部通過
- [x] 執行 profiling（3,000 次 `check_and_trade`）

## 🆕 2026-03-13 Pattern Recognition 修正回合（review follow-up）
- [x] 回復向後兼容：`LiveTradingLoop` 在 `model_manager` 無 `predict_pattern()` 時自動 fallback `Unknown`，不阻斷既有模型。
- [x] 強化穩定性：pattern snapshot CSV 寫入改為容錯路徑，避免 I/O 例外中斷主交易循環。
- [x] 訓練器邊界修正：`trainer_pattern_v1` 對小樣本 window 增加 split 防護（避免 0-size train/val）。
- [x] 風險邊界註釋：pattern 閾值放寬加入 cap 說明（最低維持 baseline 50%）。
- [x] 新增單元測試：覆蓋「無 `predict_pattern` 的舊 manager」相容場景。
- [x] 執行 `python -m pytest tests/` 全部通過。
- [x] 執行 paper trading 驗證：輸出 `PAPER_ORDER TSLA BUY ...`。
- [x] 執行 profiling（2,000 次 `check_and_trade`）。

## 🆕 2026-03-13 Issue #43 follow-up（comment 修復）
- [x] 強化相容防護：`predict_pattern` 回傳非 dict payload 時改為 warning + fallback，不中斷交易循環。
- [x] 新增回歸測試：覆蓋 malformed pattern payload 場景（string payload）。
- [x] 執行 `python -m pytest tests/` 全部通過。
- [x] 執行 paper trading 驗證：`PAPER_ORDER` 日誌可觸發。
- [x] 執行 profiling（2,000 次 `check_and_trade`）。

## 🆕 2026-03-13 Issue #43 小 bug 修復（payload + dashboard 兼容）
- [x] `predict_pattern` payload 正規化：confidence 非數值/NaN/超界時改為容錯 + clamp 到 `[0,1]`。
- [x] 新增 `RISK BOUNDARY` 註釋：防止 malformed confidence 放大風險倉位。
- [x] dashboard 兼容舊版 pattern CSV（缺 `predicted_move` 欄位時動態降級顯示）。
- [x] 新增回歸測試：覆蓋 invalid confidence payload 場景。
- [x] 執行 `python -m pytest tests/` 全部通過。
- [x] 執行 paper trading 驗證：`PAPER_ORDER` 日誌可觸發。
- [x] 執行 profiling（2,000 次 `check_and_trade`）。

## 🆕 2026-03-13 Issue #48 Data fallback 穩定性修復
- [x] `DataManager` 啟動流程改為 API key 缺失時停用 Infoway，避免無效 websocket 啟動。
- [x] `DataManager` 支援 yfinance 可選依賴，缺包環境改為 warning + 回傳 `None`，不在匯入階段中斷。
- [x] yfinance 價格讀取強化：`fast_info` 不可用時回退 `history(1d/1m)` 最新收盤價。
- [x] 新增測試 `tests/test_data_manager.py` 覆蓋 API key 缺失、`fast_info` dict、history 回退、缺 yfinance 場景。
- [x] 執行 `python -m pytest tests/` 全部通過。
- [x] yfinance fallback 再強化：僅接受有效正價格，`history(1d/1m)` 空資料時再回退 `history(5d/1d)`。
- [x] 新增回歸測試：覆蓋 intraday 空資料日線回退、`fast_info` 非正價格自動忽略。


## 🆕 2026-03-14 Risk Gate 擴充（Daily Loss + VaR/CVaR）
- [x] `RiskConfig` 新增 `max_daily_loss_fraction`、`max_trade_cvar_95`（預設維持向後兼容）。
- [x] `RiskController` 新增日內虧損 gate 與 CVaR(95) 估計 / VaR+CVaR 聯合 gate。
- [x] `LiveTradingLoop` 持久化 `day_start_equity`，每個 cycle 先做日內風險門檻檢查。
- [x] `_run_trading_logic` 在 BUY 前強制執行 daily-loss gate，並輸出明確風險邊界阻擋日誌。
- [x] 新增/擴充測試：`tests/test_risk_manager.py`、`tests/test_main_loop_trade_bridge.py`。
- [x] 執行 `python -m pytest tests/`。
- [x] 執行 Paper Trading 驗證日誌（`PAPER_ORDER ...`）。
- [x] 執行 profiling（`check_and_trade` 迴圈）。
## 2026-03-14 DataManager Data Quality Validation
- Added unified market payload validation in `data_manager.py` across Infoway/Massive/Futu-HK/yfinance.
- Added per-source quality metrics (total/invalid/missing and rates) with warning threshold logging.
- Extended `tests/test_data_manager.py` for null/negative price, malformed timestamp, and metrics increments.
