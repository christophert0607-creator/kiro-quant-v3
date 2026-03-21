# Kiro Quant 改進路線圖 (Progress.md)

> 基於每日 ML/AI Trading 研究結果整理

---

## 🚀 短期改進 (1-2週)

### 1. Optuna 超參調優 ✅ 已完成 (2026-03-17)
- 已安裝 optuna
- 已整合到 backtest engine
- Best RSI: Sharpe 1.73 (vs default 0.79)

### 2. 增加技術指標庫
- [x] RSI, MACD, VWAP, ADX ✅
- [x] Bollinger Bands ✅
- [x] ATR (Average True Range) ✅
- [x] Stochastic Oscillator ✅

### 3. 採用 skfolio 組合優化
- 與 scikit-learn 無縫集成
- 支持多種風險模型

---

## 📈 中期方向 (1-3個月)

### 1. LLM 策略生成
- 參考 QuantAgent 思路
- 利用 LLM 自動生成 trading strategies

### 2. 多因子風險模型
- 參考 toraniko 多因子股票風險模型
- 引入更多因子 (size, value, momentum, etc.)

### 3. 強化學習模組
- 參考 TradeMaster RL 框架
- DDPG 等 RL 模型進行 portfolio optimization

### 4. 模型升級
- [x] XGBoost ✅ (42.56% accuracy)
- [x] Random Forest ✅ (51.63% accuracy)
- [x] LightGBM ✅
- [x] CatBoost ✅

---

## 🔧 長期願景 (3-6個月)

### 1. 端到端自動化
- 數據 → 特徵 → 模型 → 組合優化 → 回測 → 實盤

### 2. 自我進化系統
- 參考 QuantEvolve 多智能體進化框架
- 自動發現新策略

### 3. 實盤對接
- [ ] Futu OpenD 穩定連接
- [ ] 風控優化
- [ ] 資金管理 Kelly Formula 驗證

---

## 📚 推薦資源

- **awesome-quant** (wilsonfreitas) - 量化資源大全
- **awesome-systematic-trading** (wangzhe3224) - 系統化交易庫
- **Awesome-Quant-Machine-Learning-Trading** - ML+量化資源

---

## 🛠️ Tech Stack 建議

| 類別 | 工具 |
|------|------|
| 數據處理 | NumPy, Pandas |
| 優化 | scipy.optimize, skfolio |
| ML | XGBoost, LightGBM, LSTM |
| 回測 | Backtesting.py, Backtrader, Zipline |
| 實盤 | Futu OpenD, Interactive Brokers |

---

## 🧱 Phase 1 系統硬化（2026-03-21）

### 已完成
- [x] 建立 `requirements-dev.txt`，補齊 pytest / 基本開發依賴
- [x] 建立 `pytest.ini`，統一 test discovery
- [x] 新增 `validate_config.py`，可先做 config fail-fast
- [x] 對齊 `config.json` / `config.example.json` 關鍵 schema（含 `runtime_profile`）
- [x] 建立 GitHub Actions smoke CI（config / compile / dry-run / pytest collect）
- [x] 強化 `.gitignore`，收斂 logs / DB / backup / tmp / venv 噪音
- [x] 新增 `docs/DEVELOPMENT.md` 與 `docs/PHASE1_BASELINE_2026-03-21.md`

### Phase 2（2026-03-21）
- [x] persistence schema expansion（`executions` / `position_snapshots` / `pnl_snapshots` / `risk_events` / `alerts`）
- [x] secrets / startup preflight hardening（`preflight.py`、launcher preflight、禁用預設 hardcoded fallback key）

### Phase 3（2026-03-21）
- [x] execution audit trail plumbing（gate blocks / hold decisions / execution failures / alerts 落 SQLite）
- [x] runbook / failure handling（`docs/RUNBOOK.md`）

### Phase 4（2026-03-21）
- [x] paper-trading readiness rehearsal（`docs/READINESS_REHEARSAL_2026-03-21.md`）
- [x] go/no-go scorecard：paper trading = GO，live trading = NO-GO

*Last Updated: 2026-03-21*
