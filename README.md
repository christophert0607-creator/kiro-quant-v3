# Kiro Quant V3.6 - AI量化交易系統

> **Last Updated:** 2026-03-17
> **Version:** V3.6 Flagship

基於 ML/AI 既全自動化股票交易系統，支援美股、港股、期權。

---

## 🎯 功能特點

### 核心引擎
- ✅ **V3.6 Pipeline** - 异步非阻塞执行架构
- ✅ **Kelly Sizing** - 动态凯利公式仓位管理
- ✅ **Transaction Cost Filter** - 手续费滑点门禁
- ✅ **Monte Carlo 压力测试** - 风险评估

### ML 模型
- ✅ **Random Forest** - 51.63% accuracy
- ✅ **XGBoost** - 42.56% accuracy
- ✅ **LSTM** - 48.65% accuracy
- ✅ **3-Portfolio Ensemble** - $300k 模拟资金

### 交易功能
- ✅ **Paper Trading** - 模拟交易
- ✅ **Multi-Timeframe** - 1D/4H/1H 信号
- ✅ **110 Stocks 监控** - 50-500 可扩展
- ✅ **自动止损/止盈**

### 监控
- ✅ **Watchdog Guardian** - 自动重启
- ✅ **Daily Research Cron** - 每日 GitHub 调研
- ✅ **Multi-TF Scanner** - 每日信号扫描

---

## 🚀 快速開始

### Paper Trading
```bash
cd ~/.openclaw/workspace/skills/kiro-quant
python3 v3_launcher.py
```

### Dry Run
```bash
python3 v3_launcher.py --dry-run
```

### Daily Scan
```bash
./daily_scan.sh
```

---

## 📊 模型性能

| Model | Accuracy | Stocks | Features |
|-------|----------|--------|----------|
| Random Forest | **51.63%** | 50 | 38 |
| LSTM | 48.65% | 50 | 40 |
| XGBoost | 42.56% | 50 | 38 |

### Top Features
1. Volatility_20
2. SMA_200_ratio
3. Volume_MA5
4. ATR_ratio
5. SMA_100_ratio

---

## 📁 文件結構

```
kiro-quant/
├── v3_launcher.py          # 主入口
├── v3_pipeline/            # V3.6 核心
│   ├── core/
│   ├── models/
│   └── v36/               # Kelly + Cost Filter
├── backtest_engine/        # 回測引擎
│   └── src/analysis/       # ML 模型
├── state.json              # 系統狀態
├── state_3portfolios.json  # 3倉配置
├── DEVLOG.md               # 開發日誌
└── daily_scan.sh           # 每日掃描
```

---

## ⚙️ 配置

### 3-Portfolio 設定
```json
{
  "portfolios": {
    "A_RF": {"model": "Random Forest", "capital": 100000},
    "B_XGB": {"model": "XGBoost", "capital": 100000},
    "C_LSTM": {"model": "LSTM", "capital": 100000}
  }
}
```

### Cron Jobs
- **Daily Research** - 每日 7:00 AM (HKT)
- **Hourly Review** - 每小時系統檢查

---

## 📈 歷史與近期更新

### ✨ 2026-03-17: V3.6 Flagship 全面優化與封存機制
詳見完整的 [DEVLOG.md](./DEVLOG.md) 開發日誌。本次更新專注於提升並發效率、降低手續費衝擊，並執行專案大掃除。
1. **🚀 解決 Async 非阻塞執行**：將 Futu API 網路請求外包至 `asyncio.to_thread`，徹底解除單支股票下單卡死整體迴圈結算的問題。
2. **⚡️ 消除冗餘特徵計算**：廢除 `manager.py` 中的無效率 Pandas 迴圈算式，統一由 `TechnicalIndicatorGenerator` 生成，節省大量 CPU 消耗。
3. **🛡️ 實裝 Watchdog Guardian**：新增 `v3_watchdog.sh` 守護腳本，實現自動監聽進程生存、崩潰立馬重啟與 Telegram 報警。
4. **💎 Kelly Sizing + Transaction Cost Filter**：正式切入 V3.6 資金管理模組，引入交易滑點評估門檻與蒙地卡羅(Monte Carlo)動態勝率凱利下注公式。
5. **🧹 Legacy V2 專案封存**：建立 `legacy_archive/`，將淘汰的 `quant_v2.py` 以及數以百計的繁雜 `.log` 及舊版 `HOURLY_REVIEW` 盡數封存，保護目前的 V3 極簡工作環境。

### 🤖 歷史版本模型狀態
1. Random Forest (51.63%)
2. XGBoost (42.56%)
3. LSTM (48.65%)
4. 3-Portfolio Ensemble ($300k)
5. Multi-Timeframe Scanner (110 stocks)

---

## 🔧 故障排除

```bash
# Check status
cat state.json

# Check logs
ls -la *.log

# Restart
python3 v3_launcher.py
```

---

## 📚 文檔

- [DEVLOG.md](./DEVLOG.md) - 開發日誌
- [SKILL.md](./SKILL.md) - OpenClaw Skill
- [backtest_engine/SPEC.md](./backtest_engine/SPEC.md) - 回測規格

---

**AI量化交易 · 自動化部署 · 持續優化** 🤖
