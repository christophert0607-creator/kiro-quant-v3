# Kiro Quant V3.6 發展藍圖
## 目標：成為成熟既量化交易系統 + 8×8×139 Backtest Engine

---

## 📊 當前狀況 (2026-03-17)

### ✅ 已完成

#### V3.6 基礎架構
- [x] 多數據源整合 (Futu + yfinance + OpenBB)
- [x] Paper Trading 模式
- [x] 風險控制 (止盈/止損/分散持倉)
- [x] Swing Strategy 信號 (RSI/MACD)

#### 8×8×139 Backtest Engine (NEW!)
- [x] **Phase 1: Data + Caching (100%)**
  - yfinance fetcher
  - DuckDB cache
  - Data validator (Survivorship Bias)
  - Futu fetcher wrapper
  
- [x] **Phase 2: Compute Engine (100%)**
  - Polars dataframes
  - 8 Indicators (RSI, MACD, BB, KDJ, ADX, CCI, WILLR, ATR)
  - 139 parameter variations
  - Transaction costs + Slippage model
  - Kelly Position Sizing
  - Stop Loss (5%) / Take Profit (15%)
  
- [x] **Phase 3: Portfolio & Risk (100%)**
  - Multi-Portfolio (8 markets)
  - Max 10 positions
  - Max 20% single position
  - Daily loss limit (5%)
  - Max drawdown tripwire (15%)
  
- [x] **Phase 4: WFO & Analysis (100%)**
  - DataSplitter (70/15/15)
  - Metrics (Sharpe, Sortino, Calmar, Win Rate)
  - Top-N validation
  - Output generators (CSV, JSON)

---

## 🎯 發展階段

### Phase 1: 系統修復 (Day 1-3) ✅ 完成
**目標：系統可以正常運作**

| 任務 | 說明 | 優先級 | 狀態 |
|------|------|--------|------|
| Fix Futu Failover | 連接失敗 3 次後自動切換到 yfinance | 🔴 | ✅ |
| 確認 DataManager | 確保 Infoway/yfinance fallback 工作 | 🔴 | ✅ |
| 測試交易流程 | 確保買賣指令可以發送 | 🟡 | ✅ |

**驗收標準：**
- V3.5 可以在一分鐘內進入交易狀態 ✅
- 每日有交易信號產生 ✅

---

### Phase 2: 數據收集 (Day 4-30) 🔄 進行中
**目標：收集 100+ 交易記錄**

| 任務 | 說明 | 優先級 | 狀態 |
|------|------|--------|------|
| 開始 Paper Trading | 每日記錄交易 | 🟢 | 🔄 |
| 追蹤表現 | 記錄 Sharpe/Win Rate/DD | 🟢 | 🔄 |
| 數據備份 | 每次交易後自動保存 | 🟢 | ✅ |

---

### Phase 3: 8×8×139 Backtest Engine (Day 10-20) ✅ 完成

**目標：建立完整既回測系統**

| 任務 | 說明 | 優先級 | 狀態 |
|------|------|--------|------|
| Data Layer | yfinance + Futu + DuckDB | 🔴 | ✅ |
| Indicator Engine | 8 indicators + 139 params | 🔴 | ✅ |
| Backtest Engine | Polars + Costs | 🔴 | ✅ |
| Portfolio & Risk | Multi-portfolio + Kelly | 🟡 | ✅ |
| WFO Analysis | Train/Val/Test split | 🟡 | ✅ |

**驗收標準：**
- 可以運行 8,864 種組合 ✅
- WFO 驗證完成 ✅
- Output 生成 (CSV/JSON) ✅

---

### Phase 4: 策略優化 (Day 21-30)
**目標：搵到最佳策略參數**

| 任務 | 說明 | 優先級 |
|------|------|--------|
| Optuna Bayesian | 用 Bayesian 優化參數 | 🟢 |
| 更多市場 | 加入 SG, JP, AU | 🟢 |
| 期權 Support | 加入 Options 交易 | 🟡 |

---

### Phase 5: 實際交易 (Day 31+)
**目標：開始真實交易**

| 任務 | 說明 | 優先級 |
|------|------|--------|
| 小額開始 | $1000 試水 | 🟡 |
| 逐步加碼 | 根據表現調整 | 🟡 |
| 風險監控 | 實時監控系統 | 🟢 |

---

## 📁 文件結構

```
skills/kiro-quant/
├── backtest_engine/        # NEW! 8×8×139 Backtest
│   ├── SPEC.md            # 詳細說明
│   ├── config/            # 設定檔
│   └── src/               # 源代碼
│       ├── data/           # 數據層
│       ├── indicators/     # 指標
│       ├── backtest/      # 回測引擎
│       ├── portfolio/      # 倉位管理
│       ├── risk/          # 風險控制
│       └── analysis/       # WFO & 分析
├── v36/                   # V3.6 交易代碼
├── v3_pipeline/           # Pipeline 代碼
└── ROADMAP.md            # 本文件
```

---

## 📈 驗收結果 (2026-03-17)

### 8×8×139 Backtest Engine
- ✅ Data Fetch: yfinance working
- ✅ DuckDB Cache: Ready
- ✅ Indicators: 8 types working
- ✅ Backtest: 314 tests/sec
- ✅ WFO: Train/Val/Test split working
- ✅ Metrics: Sharpe, Sortino, Calmar, Win Rate

### Best Results (AAPL/NVDA 2024)
- RSI period=14: Sharpe 1.72, Win Rate 100%
- MACD fast=5/slow=15: Sharpe 0.65

---

## 🚀 下一步

1. **整合** backtest_engine 到現有 V3.6
2. **Optuna** Bayesian 優化
3. **更多數據** - 加入 HK, A股
4. **實際交易** - 小額開始

