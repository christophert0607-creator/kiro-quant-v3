---
name: futu-api
description: |
  Futu Quant Dynamic V2.1 (Kiro Edition)
  - Dual-account training (Real/Simulation)
  - Triple-fallback data sync (Futu -> Massive -> yfinance)
  - Multi-indicator RSI/Volume/VIX monitoring
  - WSL2 auto-gateway detection
---

# 富途 API 量化雙星系統 (Kiro Edition)

## 🎯 概述
基於富途牛牛 OpenAPI 的高階自動化交易與監控技能。專為 **SOXL/TSLL** 等高波動標的設計，支持模擬倉與實盤同步運作。

## 🚀 快速開始

### 安裝依賴
```bash
cd ~/.openclaw/workspace/skills/futu-api
pip install -r requirements.txt
```

### 前置要求
1. 安裝並運行 **FutuOpenD** (Windows 宿主機)
2. 在 WSL 中確保能訪問宿主機端口 `11111`
3. 模擬倉初始資金建議設定為 **US$10,000**

## 📊 核心功能

### 1. 雙帳戶同步監控
支持同時連接實盤與模擬帳戶，出現 3星/5星 信號時自動執行：
- **⭐⭐⭐⭐⭐ (五星)**: 全指標共振，高倉位衝鋒。
- **⭐⭐⭐ (三星)**: 關鍵指標達成，試探性建倉。

### 2. Triple Fallback 數據守護
當 Futu 數據延遲或斷線時，自動切換備援路徑：
**Futu (Priority) ➔ Massive ➔ yfinance (Safety Net)**

### 3. 三位一體量化指標
- **超跌偵測**: 15m/1h RSI < 25
- **爆量驗證**: 成交量比前 20 根平均 > 1.3x
- **環境過濾**: VIX 指數 < 22 (防 Decay 神器)
- **盤口讀心**: Level 2 Order Book 買賣深度比分析

## 💻 常用指令

```bash
# 啟動 multi-symbol 全自動監控 (包含模擬倉交易)
python futu_api.py SOXL TSLL NVDA

# 查詢特定標的資金流向 (包含主力大單分析)
python futu_api.py capital SOXL --market US

# 檢查模擬倉當前持倉與盈虧
python futu_api.py assets --env simulated
```

## ⚙️ 進階配置 (WSL2)
本技能已內置宿主機自動偵測邏輯，無需手動填寫電腦 IP。系統會自動追蹤 Windows 的 `FutuOpenD` 地址。

---
**版本**: 2.1.0 (Kiro Edition)
**維護**: 小祈 (Kiro)
**適用**: 全球美股/港股量化交易玩家
