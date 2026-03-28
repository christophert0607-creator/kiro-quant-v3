# KiroQuant V3 — 進度追蹤

**最後更新**: 2026-03-28 07:55 GMT+8

---

## 🟢 系統現況

| 組件 | 狀態 |
|------|------|
| V3 Live Trading (NO_FUTU=1) | ✅ 運行中，YF-only 模式 |
| Meta-Labeling Pipeline | ✅ M0–M10 完成 |
| Decision Trace Collection | ✅ 持續收集 |
| FutuOpenD (moomoo) | ❌ 登入失敗，需密碼 |

---

## 🏗️ V3 Pipeline 架構

```
v3_launcher.py
└── LiveTradingLoop (HK/US market auto-switch)
    ├── FutuConnector (YF fallback, NO_FUTU=1 mode)
    ├── HistoryPriming (1661+ bars per symbol)
    ├── TechnicalIndicatorGenerator (20 indicators)
    ├── KiroAlphaEngine (WFA, 19/19 factors)
    ├── DataPreparer (X=(batch,40,24), y=(batch,1))
    ├── ModelManager (LSTM prediction)
    └── RiskController / StrategyFactory
```

---

## 🤖 Meta-Labeling Pipeline (dev/meta_labeling/)

| 階段 | 檔案 | 狀態 |
|------|------|------|
| M0 數據提取 + 指標 | `dataset_extractor.py` | ✅ |
| M1 標籤生成 | `label_generator.py` | ✅ |
| M2 Baseline Model | `baseline_model.py` | ✅ |
| M3 Backtest Harness | `backtest_harness.py` | ✅ |
| M4 Inference | `inference.py` | ✅ |
| M5 Integration Test | `integration_test.py` | ✅ |
| M6 Live Integration | `meta_gate.py` (in v3_pipeline/ml/) | ✅ |
| M7 Continuous Learning | `continuous_learning.py` | ✅ |
| M8 Performance Monitor | `performance_monitor.py` | ✅ |
| M9 Rollout Validation | `rollout_validation.py` | ✅ |
| M10 Production Ready | `PRODUCTION_READY.md` | ✅ |

**Model Performance:**
- 68 events (34 labeled: 5 positive / 29 negative)
- Horizon: 30m + 60m
- AUC: 1.0 | CV Accuracy: 75.8%±17.1%
- Threshold 0.6+ → 100% win rate (backtest)

---

## 📊 持倉狀態 (state.json)

- NVDA: 0
- GOOGL: 0
- Last trade: 2026-03-27 (GOOGL SELL, qty=2, px=278.47)

---

## 🔧 最近變更 (未 Commit)

### v3_launcher.py
- 加入 HK/US market auto-switch
- `IDLE_COLLECTION_SYMBOLS` 模式
- `polling_seconds` 改為 60s（配合 YF 1m candle）
- `auto_trade` 可配置

### v3_pipeline/core/main_loop.py
- `_sync_sentiment()` 情緒分析
- `bucket_fractions` / `bucket_thresholds` 資本分配
- `sell_signal_streak_by_symbol` 防震盪賣出
- `decision_trace` ML 學習日誌（寫入 `learning/us_sim/`）
- `latest_ind` 技術指標戰術入場

### v3_pipeline/core/futu_connector.py
- `NO_FUTU=1` 純 YF 模式
- `ENABLE_DECISION_TRACE=1` decision logging

### v3_pipeline/core/strategy_factory.py
- `choose_profile(vix, sentiment)` 根據情緒調整風險

---

## ⚠️ 待解決

1. **FutuOpenD moomoo 登入** — 帳號 `23835053`，密碼 Tsukii1993 失敗（登入 moomoo app 用密碼？）
2. **Decision Trace 落後** — V3 已停，ML 數據收集中斷

---

## 🚀 啟動指令

```bash
# Data Collection（NO_FUTU mode，不交易）
NO_FUTU=1 python3 kiro-quant-v3/v3_launcher.py

# 啟用 Meta-Labeling
ENABLE_META_LABELING=1 META_LABELING_MODE=us_sim META_THRESHOLD=0.6 NO_FUTU=1 python3 kiro-quant-v3/v3_launcher.py
```
