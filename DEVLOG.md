# 開發日誌 (DEVLOG)

## 日期: 2026-03-17
**主題: V3.6 旗艦版架構優化與舊版封存 (Kiro Quant V3.6 Flagship Optimization)**

本次更新專注於提升 Kiro Quant **V3.6 (`v3_pipeline`) 旗艦版**的並發執行效能、消除代碼冗餘，並深度整合進階的 V3.6 資金管理模組，最後對專案進行全面大掃除，封存淘汰的 V2 程式碼。

### 🚀 1. 解決非同步事件迴圈阻塞 (Non-blocking Execution)
- **問題：** 發現 `v3_pipeline/core/main_loop.py` 中的 `_execute` 方法調用 Futu 下單 API (`futu_connector.place_order`, `wait_for_fill`) 時使用同步堵塞等待，導致單一股票下單時，會卡死整個 Event Loop，影響其他股票的並發報價更新與模型預測。
- **解決：** 利用 `asyncio.to_thread` 將 Futu 的網路 I/O 操作外包到另一個 Thread，並將調用鏈路 (`check_and_trade`, `_run_trading_logic`) 全面升級為 `async/await` 非同步設計。現在系統的擴展性更強，執行時不再卡頓。

### ⚡️ 2. 消除冗餘特徵計算 (Indicator Optimization)
- **問題：** 在 `v3_pipeline/models/manager.py` 中的回退機制 (`_classify_pattern_rule_based`) 裡，發現仍在使用純 Pandas 的 `rolling` 和 `ewm` 冗餘重算技術指標 (EMA, Bollinger Bands, ATR)。
- **解決：** 完全清理掉這段手算程式碼，改為信任流水線前段 `TechnicalIndicatorGenerator` 所計算並掛載於 DataFrame 上的指標。同時統一了 `EMA_12`, `BB_UPPER` 等特徵命名。大幅節省了並發運算時不必要的 CPU 消耗。

### 🛡 3. 新增自動守護進程 (Watchdog Guardian)
- **新增內容：** 創建了 `v3_watchdog.sh` Bash 守護腳本。
- **功能：** 此腳本會在背景監控 `v3_launcher.py` 是生存活。一旦偵測到程序異常崩潰，將會**自動重啟**交易程序，並透過 Node 傳送訊號給 Telegram 進行錯誤報警 (Alert)。

### 💎 4. V3.6 進階風控模組深度整合 (Kelly Sizing & Cost Matrix)
- **問題：** `v3_pipeline` 過去的部位控管較為靜態，且僅仰賴信心度與固定比例上限，缺乏真實的盈虧比期望值與手續費滑點檢核。
- **解決 (修改 `monte_carlo.py`)：** 讓蒙地卡羅壓力測試除送出勝率外，額外暴露 `avg_win` 與 `avg_loss` 期望值給主迴圈。
- **解決 (修改 `main_loop.py`)：** 
    1. 正式實例化 `v36/transaction_cost.py` (TransactionCostCalculator)。在模型給出買點時，嚴格計算**手續費加滑點是否會吃掉預期獲利** (且含 20% 緩衝)，無利可圖的信號直接阻擋。
    2. 正式實例化 `v36/kelly_position_sizer.py` (KellyPositionSizer)。依據歷史勝率與賠率，套用**凱利公式 (Kelly Formula)** 計算最佳下注比例，完全捨棄舊有的簡單定額或信心度資金比例機制。真實達成了 V3.6 高級資金管理目標。

### 🧹 5. 專案結構大掃除與 V2 封存 (Legacy Archiving)
- **行動：** 將所有過去已經淘汰的 V2 版本文件與歷史雜亂日誌移入 `legacy_archive/` 供備查，確保跟目錄與工作區保持簡潔，以 V3.6 為絕對核心。
- **封存項目包含：** 
    - 舊版腳本：`quant_v2.py`, `quant_system.py`, `feature_engineering.py`, `risk_guard.py` 等十多個檔案。
    - 舊版執行日誌：數百個肥大的 `v3_live*.log` 與 `.gz` 日誌檔。

---
**下一階段目標建議：**
- 進行一次 Dry-Run 或 Paper Trading 模擬，以驗證 Kelly 公式和交易成本門檻阻擋的精確性。
- 將 `DEVLOG.md` 持續更新，作為系統演進的核心文獻紀錄。

---

## 日期: 2026-03-18
**主題: HK Stocks 數據修復與交易參數優化**

### 🔧 1. HK Stocks Yahoo Finance Ticker Format Fix
- **問題：** HK stocks (如 `0700.HK`) 被標記為 "possibly delisted"
- **原因：** `data_manager.py` 將 `0700.HK` 錯誤拆分為 `HK`
- **解決：** 新增 `.HK` suffix 檢查，保留完整 ticker 格式
- **檔案：** `data_manager.py` (lines 178-184, 326-331, 354-360)

### 🛡️ 2. 移除故障股票 (0155.HK)
- **問題：** 0155.HK 數據源反復失敗 (30+ errors)
- **解決：** 從 `config.json` 和 `v36_config.json` 移除

### 📊 3. 安裝 TA-Lib
- **問題：** TA-Lib 缺失，使用 pandas fallback
- **解決：** `pip install ta-lib`
- **影響：** 更精確的技術指標計算

### 🎯 4. 四次連續信號過濾器 (4-Consecutive Signal Filter)
- **問題：** 交易過於頻繁，每 15 秒觸發信號
- **解決：** 新增 `consecutive_swing_buy_signals` 計數器
- **邏輯：** 需要 4+ 次連續 swing buy 信號才執行買入
- **檔案：** `v3_pipeline/core/main_loop.py`

### ⚙️ 5. 交易參數優化
| 參數 | 之前 | 之後 |
|------|------|------|
| Threshold | 0.5% | 1.0% |
| Polling | 15 秒 | 60 秒 |
| Max Positions | 10 | 10 (保持) |

### 📈 6. HK Stocks Monitor 擴展
- **Symbols:** 20 隻 HK stocks (移除 0155.HK 後 19 隻)
- **名單：** 0700.HK, 9988.HK, 3690.HK, 1024.HK, 2318.HK, 1299.HK, 0939.HK, 0005.HK, 0388.HK, 0960.HK, 1109.HK, 0941.HK, 0175.HK, 1810.HK, 2688.HK, 2269.HK, 1211.HK, 2018.HK, 0688.HK

### 📁 7. 新增進度追蹤文檔
- **檔案：** `PROGRESS.md`
- **內容：** 短期/中期/長期改進路線圖

---
- 考慮進一步提高 threshold 或增加 required signals
- 實行 skfolio 組合優化與 GBM 模型對抗測試

---

## 日期: 2026-03-18 (晚間更新)
**主題: 技術指標庫擴展、GBM 模型升級與 skfolio 組合優化整合**

本次更新大幅增強了 V3.6 旗艦版的特徵工程能力與模型多樣性，並引入了專業級的組合優化工具。

### 🚀 1. 技術指標庫全面擴展 (Indicators Library Expansion)
- **新增指標：** 在 `v3_pipeline/features/indicators.py` 中新增了 `VWAP` (成交量加權平均價) 與 `Stochastic Oscillator` (KDJ, %K/%D)。
- **優化：** 補全了 `Bollinger Bands` 與 `ATR` 的回退(fallback)計算邏輯。
- **穩定性：** 在所有分母運算中加入了 `1e-10` 保護，並強化了 NaN 填充邏輯，有效防止 `repro_nan.py` 中遇到的數據污染問題。

### 🧠 2. 引入 GBM 模型管理員 (XGBoost, LightGBM, CatBoost)
- **新增內容：** 在 `v3_pipeline/models/manager.py` 中創建了 `GBMModelManager`。
- **功能：** 支持單一接口調用三種主流樹型模型。相較於 LSTM，這些模型在非時間序列強相關的因子預測上通常具有更好的精確度與訓練速度。
- **流水線：** 新增 `train_gbm_pipeline` 函數，支持自動生成 5 日漲跌標籤並完成模型訓練與保存。

### 📈 3. 整合 skfolio 組合優化 (Advanced Portfolio Optimization)
- **新增內容：** 在 `v3_pipeline/portfolio/portfolio.py` 中整合了 `skfolio` 庫。
- **算法：** 實現了 `optimize_with_skfolio` 方法，支持 **最大夏普比率 (Maximum Sharpe)** 與 **等風險貢獻 (Equal Risk Contribution/ERC)** 算法。
- **靈活性：** 允許主迴圈根據歷史收益率動態調整部位權重，而不僅僅依賴於規則導向的固定比例。

### 📦 4. 依賴環境更新
- **檔案：** `v3_pipeline/requirements.txt`
- **新增：** `xgboost`, `lightgbm`, `catboost`, `skfolio`。

---
**下一階段目標：**
- 執行 `GBMModelManager` 的基準測試，對比 LSTM 與 XGB/LGBM 的預測準確率。
- 在回測中啟用 `skfolio` 優化，驗證其相較於等權重組合的夏普比率提升。
- 檢查 `repro_nan.py` 以確保所有指標計算在極端數據下依然強健。
