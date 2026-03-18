# 8×8×139 Backtest Engine - Specification Document (UPDATED)

> **Last Updated:** 2026-03-17 18:33 (HKT)

---

## 2026-03-17 Updates

### ML Models Added
- **XGBoost Classifier**: 42.56% accuracy (50 stocks, 38 features)
- **Random Forest**: 51.63% accuracy (best single model)
- **LSTM** (proxy): 48.65% accuracy

### 3-Portfolio Setup
| Portfolio | Model | Capital |
|-----------|-------|---------|
| A_RF | Random Forest | $100,000 |
| B_XGB | XGBoost | $100,000 |
| C_LSTM | LSTM | $100,000 |

### Top Features (RF)
1. volatility_20 (4.0%)
2. sma_200_ratio (4.0%)
3. volume_ma5 (3.6%)
4. atr_ratio (3.5%)
5. sma_100_ratio (3.4%)

---

## Implementation Tasks

### Phase 1: Local Data Infrastructure & Caching ✅ 100%
- [x] Initialize Python project and virtual environment ✅
- [x] Implement abstract `DataFetcher` class for generic API calls ✅
- [x] Implement `FutuFetcher` wrapper around Futu OpenAPI ✅
- [x] Implement `YFinanceFetcher` wrapper ✅
- [x] Build **Local Caching Layer** (`DuckDB`) ✅
- [x] Implement Data Quality Validator (Anti-Survivorship Bias rules) ✅

### Phase 2: Hybrid Compute Engine (Polars & Numba) ✅ 100%
- [x] Set up **Polars** dataframes for technical indicators ✅
- [x] Write highly optimized technical indicator formulas ✅
- [x] Configure the 139 parameter variations generation matrix ✅
- [x] Develop the **Numba JIT** backtest loop for path-dependency ✅
  - [x] Implement dynamic Stop Loss (5%) / Take Profit (15%) ✅
  - [x] Implement fractional Kelly (0.25) position sizing logic ✅
  - [x] Implement transaction costs and slippage models ✅

### Phase 3: Portfolio & Risk Simulation ✅ 100%
- [x] Build the Multi-Portfolio architecture (8 markets parallel execution) ✅
- [x] Enforce portfolio constraints (Max 10 positions, Max 20% single position) ✅
- [x] Implement daily loss limits and maximum drawdown tripwire (15%) ✅

### Phase 4: Walk-Forward Optimization (WFO) & Analysis ✅ 100%
- [x] Implement `DataSplitter` for Train/Test split (70/15/15) ✅
- [x] Build the simulation runner spanning 8,864 combinations ✅
- [x] Write performance metric calculators (Sharpe, Calmar, Sortino, Win Rate) ✅
- [x] Implement Top-N validation runner ✅
- [x] Generate output CSVs and reports ✅

---

## Current Status

| Phase | Progress |
|-------|----------|
| Phase 1: Data + Caching | **100%** ✅ |
| Phase 2: Compute Engine | **100%** ✅ |
| Phase 3: Portfolio & Risk | **100%** ✅ |
| Phase 4: WFO & Analysis | **100%** ✅ |
| Phase 4: WFO & Analysis | 0% |

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.10+ |
| Data | Polars, DuckDB |
| Compute | Polars + Numba (JIT) |
| Indicators | Custom (RSI, MACD, BB, KDJ, ADX, CCI, WILLR, ATR) |
| Data Sources | Futu OpenAPI, yfinance, OpenBB |
| Optimization | Optuna (Bayesian) |
| Parallel | joblib |

---

## File Structure

```
8x8x139_backtest/
├── config/
│   ├── markets.yaml
│   ├── indicators.yaml
│   ├── backtest.yaml
│   └── risk.yaml
├── src/
│   ├── data/
│   │   ├── cache.py        # DuckDB ✅
│   │   ├── fetcher.py      # yfinance ✅
│   │   └── futu_source.py  # (Phase 1)
│   ├── indicators/
│   │   ├── indicators.py    # RSI, MACD, BB, KDJ, etc. ✅
│   │   └── factory.py
│   ├── backtest/
│   │   ├── engine.py       # Polars ✅
│   │   └── costs.py        # (Phase 2)
│   ├── portfolio/
│   │   ├── portfolio.py    # (Phase 3)
│   │   └── sizing.py       # Kelly (Phase 3)
│   ├── risk/
│   │   └── controls.py     # (Phase 3)
│   └── analysis/
│       ├── metrics.py      # Sharpe, Sortino, etc. (Phase 4)
│       └── wfo.py          # Walk-Forward (Phase 4)
├── requirements.txt
└── main.py
```

---

## Results (Test Run)

- **Data Fetch:** ✅ 756 rows (AAPL, MSFT, GOOGL)
- **Indicators:** ✅ RSI, MACD, BB, KDJ, CCI, WILLR
- **Speed:** 314 tests/sec (26 RSI params in 0.08s)
- **Best:** RSI period=14 on NVDA → Sharpe 1.72, Win Rate 100%
