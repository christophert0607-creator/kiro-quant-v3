# Kiro Quant V3 — 智能量化交易系統

> 即時股價預測 × 機器學習 × 自動化交易的 AI 交易引擎

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🧭 系統總覽

```
┌─────────────────────────────────────────────────────────────────┐
│                      Kiro Quant V3                               │
│                                                                  │
│   行情攝取 ──→ 特徵工程 ──→ LSTM/XGBoost 預測 ──→ 交易決策    │
│       ↓              ↓                    ↓                    │
│   FutuConnector  TechnicalIndicatorGenerator  LiveTradingLoop  │
│   yfinance       DataPreparer              ModelManager          │
│   QuoteCache                                               ↓    │
│                                                      RiskController
│                                                      KellyPositionSizer
└─────────────────────────────────────────────────────────────────┘
```

---

## 📐 架構地圖（Graph Insight）

本系統由 **2,440 個節點**、**4,299 條關係**組成，分為 **111 個社群**。

### 核心節點（最多連接）

| 節點 | 連接數 | 職責 |
|------|--------|------|
| `FutuConnector` | 127 | 富途 OpenD 適配器，多源行情 fallback |
| `LiveTradingLoop` | 77+32 | 核心交易循環引擎 |
| `FutuConfig` | 65 | OpenD 連線配置管理 |
| `TechnicalIndicatorGenerator` | 64 | 技術指標計算（RSI/MACD/BB/CCI/WILLR） |
| `ModelManager` | 43 | 模型載入、元資料驗證 |
| `DataPreparer` | 41 | 訓練數據準備、特徵標準化 |
| `LiveConfig` | 34 | 實時交易配置動態管理 |
| `RiskController` | 33 | 風控規則引擎 |

### 十一大功能社群

| 社群 | 節點數 | 核心模組 |
|------|--------|---------|
| **行情層**（Community 0, 7） | 126 | `FutuConnector`, `QuoteCache`, `AbstractDataFetcher`, `yfinance provider` |
| **模型訓練**（Community 1, 39, 44） | 132 | `KiroLSTM`, `XGBoostClassifier`, `DataPreparer`, `self_learn/retrain.py` |
| **配置管理**（Community 2, 12） | 95 | `ConfigManager`, `V36Config`, `RiskConfig` |
| **交易引擎**（Community 9, 13） | 50 | `AdaptiveStrategy`, `MarketContext`, `MarketRegimeDetector` |
| **風控系統**（Community 24, 41, 47） | 33 | `KellyPositionSizer`, `RiskController`, `RiskRulesEngine` |
| **歷史回測**（Community 6, 27） | 60+ | `BacktestEngine`, `WFORunner`, `WalkForwardAnalysis` |
| **閒置排程**（Community 5） | 60 | `IdleTaskScheduler`, `_emit_fd_health()`, 收盤後數據預填充 |
| **因子引擎**（Community 3） | 43 | `KiroAlphaEngine`, `FinLabStyleFactorRanking` |
| **數據管理**（Community 4, 8, 25） | 117 | `DuckDBCache`, `DataManager`, `DatabaseManager` |
| **代理協作**（Community 18） | 10 | `QuantOrchestrator`, `BacktesterAgent`, `DataFetcherAgent` |
| **維基化記錄**（Community 29, 30） | 40 | `WikiWriter`, `full_wiki_briefing()`, 交易決策維基化 |

---

## 📁 目錄結構

```
kiro-quant-v3/
├── v3_launcher.py              # 引擎起動程序
├── v3_pipeline/
│   ├── core/
│   │   ├── main_loop.py         # LiveTradingLoop（心臟）
│   │   ├── futu_connector.py    # FutuConnector（行情+交易）
│   │   ├── quote_cache.py       # QuoteCache（TTL=30s 行情緩存）
│   │   └── market_context.py    # MarketContext（HK/US 市場隔離）
│   ├── config/
│   │   └── manager.py           # ConfigManager（P0 配置單一真相源）
│   ├── models/
│   │   ├── brain.py             # KiroLSTM
│   │   ├── manager.py           # ModelManager（checkpoint 驗證）
│   │   └── registry.py          # ModelRegistry（元資料驗證）
│   ├── features/
│   │   └── indicators.py        # TechnicalIndicatorGenerator
│   ├── execution/
│   │   └── state_machine.py     # ExecutionStateMachine
│   ├── data/
│   │   ├── abstract_fetcher.py  # AbstractDataFetcher + FallbackDataFetcher
│   │   ├── yf_provider.py       # 集中式 yfinance 接入（含 FD 健康監控）
│   │   └── downloader.py        # HistoricalDataDownloader
│   ├── risk/
│   │   ├── risk_controller.py   # RiskController
│   │   ├── position_sizer.py    # KellyPositionSizer
│   │   └── transaction_cost.py  # TransactionCostCalculator
│   ├── idle/
│   │   └── task_scheduler.py    # IdleTaskScheduler（收盤後預填充）
│   └── strategies/
│       ├── adaptive.py          # AdaptiveStrategy
│       └── regime_detector.py   # MarketRegimeDetector
├── self_learn/
│   ├── retrain.py               # 模型再訓練腳本
│   ├── backfill_indicators.py   # 技術指標回填腳本
│   ├── trading_bot.db           # 信號數據庫（1222 信號，589 已關閉）
│   └── models/                  # 保存的模型 checkpoint
├── tests/                       # 357 個測試
├── graphify-out/                # 知識圖譜輸出
│   └── GRAPH_REPORT.md
└── logs/
    ├── v3_live.log              # 實時心跳日誌
    └── dashboard-v3-launcher.*  # Launcher stdout/stderr
```

---

## 🔄 核心流程

### 實時交易循環（LiveTradingLoop）

```
每 60 秒一次 cycle：

1. PREFETCH ──→ QuoteCache / yfinance / FutuOpenD 批量取行情
2. SCORING  ──→ 對 watchlist 股票評分（LSTM 預測 + 技術指標確認）
3. FILTER    ──→ 置信度閾值過濾，合併 Kelly 倉位計算
4. EXECUTE   ──→ 模擬/實盤買賣（auto_trade=true 時）
5. LOG       ──→ 寫入 decisions.jsonl、更新 state.json
6. IDLE      ──→ 非交易時段執行 IdleTaskScheduler（預填充明日數據）
```

### 市場模式判定

```
HK 交易時段：09:30–12:00、13:00–16:00（HKT）
US 交易時段：22:30–05:00 HKT（翌日）
                    ↓
         MarketRegimeDetector
                    ↓
         HK / US / IDLE 三選一
```

### 風控層

```
Position Limit：15 持股上限
Stop Loss：-2% 自動止損
Take Profit：+2% 快速獲利了結
Kelly Fraction：默認 0.25（1/4 Kelly）
Max Hold：30 個 bars（收盤前強制平倉）
```

---

## 🧪 測試

```bash
# 全部測試
python -m pytest tests/ -v

# 關鍵模組測試
python -m pytest tests/test_model_registry.py tests/test_opend_stability.py -v

# 最新結果：357 passed, 1 skipped（TA-Lib 選配）
```

---

## 📊 運行狀態

| 指標 | 數值 |
|------|------|
| 總信號 | 1222 |
| 已關閉 | 589 |
| 開放中 | 361 |
| 模型準確率 | 0.79（真實，早期停頓） |
| 日均交易 | ~20 筆（HK + US） |

---

## 🔧 常用指令

```bash
# 起動引擎（行情行 Alltick，交易行 OpenD）
NO_FUTU_QUOTE=1 python v3_launcher.py

# 檢查引擎狀態
ps aux | grep v3_launcher
tail -20 logs/v3_live.log

# 檢查持倉
sqlite3 self_learn/trading_bot.db "SELECT symbol, status, qty, entry_price FROM signals WHERE status='OPEN';"

# 重建知識圖譜
python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"
```

---

## 🚨 常見問題

| 徵狀 | 解決方案 |
|------|---------|
| `ECONNREFUSED` | 確認 OpenD 運行中：`ps aux \| grep FutuOpenD` |
| `Too many open files` | FD leak，見 `references/v3-quote-diag.md` |
| `PREFETCH Completed: 0/N` | 行情全部失效，檢查 yfinance / Alltick 連線 |
| `circuit_broken: true` | 熔斷觸發，檢查 state.json 的 `consecutive_errors` |

完整疑難排解見：`references/v3-quote-diag.md`

---

## 📈 依賴關係圖（Community 視角）

```
行情層（Community 0, 7）
  QuoteCache ──→ AbstractDataFetcher ──→ [yfinance, FutuQuoteFetcher, EFinanceFetcher]

模型層（Community 1, 44）
  DataPreparer ──→ KiroLSTM / XGBoost ──→ ModelManager ──→ ModelRegistry

執行層（Community 9, 13）
  MarketRegimeDetector ──→ AdaptiveStrategy ──→ ExecutionStateMachine

風控層（Community 24, 41, 47）
  RiskController ──→ KellyPositionSizer ──→ RiskRulesEngine

閒置層（Community 5）
  IdleTaskScheduler ──→ yf_provider（FD 健康監控）──→ 維基化記錄（Community 29）
```

---

*最後更新：2026-05-08 | Graph: 2440 nodes, 4299 edges, 111 communities*
