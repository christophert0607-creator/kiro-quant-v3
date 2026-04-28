# KiroQuant V3 — 進度追蹤

**最後更新**: 2026-04-17 00:10 GMT+8

---

## 🟢 系統現況

| 組件 | 狀態 |
|------|------|
| V3 Live Trading (NO_FUTU=1) | ✅ 運行中，YF-only 模式 |
| Meta-Labeling Pipeline | ✅ M0–M10 完成 |
| Decision Trace Collection | ✅ 持續收集（US SIM mode，2026-03-28 → ongoing） |
| FutuOpenD (moomoo) | ✅ 已修復（2026-03-07 reconnect），NO_FUTU=1 純 YF fallback |

---

## 🔌 Model Provider 架構

| Provider | Model | 用途 |
|----------|-------|------|
| **minimax-cn** | MiniMax-M2.7 | 主要 Cron Jobs |
| **antigravity-proxy** | gemini-3-flash | Primary Cron Provider ✅ |
| **antigravity-proxy** | gemini-3.1-pro-high | Failover |
| **openai-codex** | gpt-5.2 | Coding Tasks |

> 所有 Cron Jobs 已切換至 `antigravity-proxy/gemini-3-flash`，failover chain: antigravity → minimax-cn

---

## ⏰ Active Cron Jobs

| Job | Schedule | Model | Status |
|-----|----------|-------|--------|
| `selfheal-detect` | 每4小時 | antigravity/gemini-3-flash | ✅ |
| `selfheal-fix` | 02/08/14/20時 | antigravity/gemini-3-flash | ✅ |
| `system-daily-health-check` | 每日 09:00 HKT | antigravity/gemini-3-flash | ✅ |
| `daily-email-summary` | 每日 07:00 HKT | antigravity/gemini-3-flash | ✅ |
| `raw-data-graphify-update` | 每日 03:00 HKT | antigravity/gemini-3-flash | ✅ |
| `kiro-quant graphify rebuild` | 每日 05:00 HKT | antigravity/gemini-3-flash | ⚠️ 2次連續error |
| `nobunaga-llm-graph-sync` | 每日 04:15 HKT | antigravity/gemini-3-flash | ✅ |
| `US Post-Close Analysis Report` | 一次性 | antigravity/gemini-3-flash | ✅ |
| `kiro-quant-daily-research` | 每日 23:00 HKT | antigravity/gemini-3-flash | ⚠️ timeout |

**已停用 Cron Jobs：**
- `Kiro Quant 美股巡航狀態` — 2026-04-11 起停用
- `Kiro Quant 健康檢查 (15m)` — 2026-04-11 起停用
- `US SIM Layer0 Account Snapshot (5m)` ×3 — 2026-04-11 起停用
- `V3 Heartbeat - Monitor & Restart if Dead` — 2026-04-11 起停用
- `US Market Trading Agent Pulse (30m)` — 2026-04-11 起停用
- `US SIM Daily Learning Report (EOD)` — 2026-04-11 起停用
- `Meta-labeling P0 Hourly Dev Loop` — 2026-04-11 起停用
- `Underworld Alice cron` — Alice phase 完成後停用

**FutuOpenD 端口：** 11113 (PID 3191320)

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
        └── choose_profile(vix, sentiment)  ← 風險 profile 動態切換
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

## 📊 Decision Trace Collection

- **Status**: ✅ 持續運行（US SIM mode）
- **Location**: `learning/us_sim/decision_trace_us_sim.jsonl`
- **Collection Period**: 2026-03-28 → ongoing（從chat記錄確認）
- **Also**: `learning/us_sim/account_snap_us_sim.jsonl`（每5分鐘 snapshot）

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
- `choose_profile(vix, sentiment)` 根據情緒調整風險（Momentum / Mean-Reversion / Grid 三種 profile）
- `confidence_to_risk_pct()` 信心指數 → 風險比例
- `trailing_stop_by_volatility()` 根據波動率追蹤止損
- `apply_commission_erosion()` 佣金侵蝕計算

---

## ⚠️ 待解決

1. **FutuOpenD moomoo 登入** — ✅ 已修復（2026-03-07），現以 NO_FUTU=1 純 YF 模式運行
2. ~~Decision Trace 落後~~ — ✅ 已排除，collection 持續運行中

---

## 🚀 啟動指令

```bash
# Data Collection（NO_FUTU mode，不交易）
NO_FUTU=1 python3 kiro-quant-v3/v3_launcher.py

# 啟用 Meta-Labeling
ENABLE_META_LABELING=1 META_LABELING_MODE=us_sim META_THRESHOLD=0.6 NO_FUTU=1 python3 kiro-quant-v3/v3_launcher.py
```

---

## 📝 文檔更新記錄

- **2026-04-17 00:10**: 新增 Active/Disabled Cron Jobs 完整列表（FutuOpenD 11113端口）、kiro-quant graphify rebuild 警告、daily-research timeout 警告
- **2026-04-17 00:04**: 全面更新（AntiGravity provider、Cron Jobs、Decision Trace 狀態、FutuOpenD reconnect）
- **2026-03-28**: 初次建立
