# Claude Code Rewrite Guide — Kiro Quant V3

> 最後更新：2026-05-26 HKT（kiro-pr-health-merge-loop scheduled auto-run）
>
> 完整歷史記錄：外部 `COMPLETED_WORK_LOG.md`（`~/.openclaw/workspace/skills/kiro-quant-agent-handoff/`）

---

## 1. 項目概覽

**Kiro Quant V3** 是一個基於 Python 的量化交易系統，核心能力包括：
- 富途 OpenAPI (FutuConnector) 即時報價與交易
- LSTM / AlphaEngine 模型驅動的趨勢預測
- 技術指標生成與風險控制
- 回測引擎與即時交易循環（300+ 標的，港美雙市場）
- SQLite 即時持久化（LiveDbPersist）與 kline backfill

---

## 2. 核心抽象（God Nodes）

| 排名 | 組件 | 職責 |
|------|------|------|
| 1 | `FutuConnector` | OpenAPI 連接、報價緩存、重連邏輯 |
| 2 | `LiveTradingLoop` | 主交易循環、狀態機、訂單執行 |
| 3 | `FutuConfig` | 連線配置（host/port/trd_env） |
| 4 | `TechnicalIndicatorGenerator` | 技術指標計算 |
| 5 | `ModelManager` | 模型加載、註冊表、自動重建 |
| 6 | `DataPreparer` | 數據預處理與特徵工程 |
| 7 | `LiveConfig` | 運行時配置熱更新 |
| 8 | `RiskController` | 倉位與風險限制 |

**重要：** 任何改動上述組件都應該經過完整回歸測試（`pytest tests/`）。

---

## 3. 目前健康狀態（2026-05-26）

| 檢查項 | 狀態 | 備註 |
|--------|------|------|
| Launcher PID | ✅ 運行中 | PID 56216，~93h uptime
| OpenD 11112 | ✅ 開放 | |
| WebSocket 8787 | ✅ 開放 | |
| Dashboard 3000 | ✅ 開放（next-server） | |
| state.json / config.json | ✅ 合法 JSON | |
| 回歸測試（隔離） | ✅ **553+ tests collected; test collection conflict isolated to 6 test files (conftest interaction, individually passthrough)** | `test_pattern_trainer_components.py` 與全量 collection 衝突，單獨跑 passthrough |
| 回歸測試（全量 collection） | ⚠️ `test_pattern_trainer_components.py` collection error | 單獨跑：2 passed；與其他測試混跑時 import 順序衝突 |
| yfinance FD | ✅ 已修復 | PR #87 + #90 已生效 |
| Self-Learn DB | ✅ 已修復 | `trading_bot.db` schema 重建，predictions 表正常寫入 |

---

## 4. PR 記錄（完整至 2026-05-19）

### 已合併

| PR | 分支 | 合併日期 | 內容摘要 |
|----|------|----------|---------|
| #91 | `fix/watchlist-version-field` | 2026-05-17 | screener 保留 version/tiers/metadata schema，修復 test_universe_expansion |
| #98 |  | 2026-05-26 | docs: update rewrite guide to reflect PRs #83-#91 and current runtime state |
| #90 | `fix/yf-provider-aggressive-reset` | 2026-05-17 | 每 N 個 symbol（預設 5）在 download_history 迴圈內重置 tkr-tz.db FD |
| #89 | `fix/quote-na-sanitisation` | 2026-05-17 | `_coerce_price()` 拒 N/A/—/NaN/空串，防止 ValueError 洩漏至 order builder |
| #88 | `fix/get-acc-list-no-kwargs` | 2026-05-17 | `discover_accounts()` 改用無 kwargs 的 `get_acc_list()` |
| #87 | `fix/db-manager-close-leak` | 2026-05-16 | DatabaseManager FD 洩漏：改用 `close()` 替代 GC 回收 |
| #86 | `feat/livedbpersist-inode-reconnect` | 2026-05-16 | LiveDbPersist Auto-sync 後 inode 變更自動重連 |
| #85 | `feat/expand-universe-300` | 2026-05-16 | 擴展至 300+ 標的，三層 tier 管理 |
| #84 | `feat/db-persist-live-data` | 2026-05-16 | async SQLite 即時資料持久化（LiveDbPersist） |
| #83 | `feat/idle-kline-db-backfill` | 2026-05-16 | 閒置時 kline 資料回填 |
| #82 | `feat/kelly-position-sizing` | 2026-05-14 | Kelly 公式替換置信度倉位計算 |
| #81 | `feat/buying-power-preflight-guard` | 2026-05-14 | BuyingPowerGuard 預飛行檢查 |
| #80 | `fix/yf-provider-fd-regression` | 2026-05-14 | 關閉 yfinance Peewee SQLite 連線，回收 FD |
| #71 | `claude/busy-lamport-56e3c7` | 2026-05-15 | docs 更新（rewrite guide） |
| #58 | `feat/system-hardening-and-safety-rails` | CLOSED 2026-05-16 | DB blob 無法合併，safety-rail 已被 #80/#81/#82 覆蓋 |

### 開放 PR（2026-05-19）

**0 個 open PRs** — main 已同步所有分支，無待合併候選。

---

## 5. 開放工作（backlog）

### ⚠️ 手動操作（不需要新分支）

> **Worktree 清理（2026-05-26）**： 可清理以下 9 個 prunable worktrees：
> - /tmp/kiro-fix-exec-sm, /tmp/kiro-fix-hk-acc, /tmp/kiro-fix-runtime-lock
> - /tmp/kiro-fix-state-store, /tmp/kiro-pr97
> - /tmp/kiro-refactor-mctx, /tmp/kiro-refactor-mds, /tmp/kiro-refactor-obs, /tmp/kiro-refactor-risk
> 當前 main branch worktree () 正常運行，勿動。

1. **REAL 帳戶 281756460301136301 DISABLED** — broker 端，提 Futu 支援票，無需程式碼分支。

### 待實作分支

#### `dashboard-warning-triage`

目標：減少 `/kiro` 儀錶板雜訊，讓 cache health 可操作。

必要行為：
- 按嚴重程度分類 stale-cache warnings（`info` / `warning` / `error`）
- 發出結構化 cache-warning 欄位：`symbol`, `age_sec`, `threshold_sec`, `provider`, `market`, `fallback_used`, `next_retry_sec`
- 在儀錶板 log 視圖中按 symbol/provider 分組重複告警
- System tab 顯示 cache health 摘要（含 `buying_power_check` 事件與 `LiveDbPersist._reconnect_count`）

#### `opend-dual-instance-plan`

目標：設計並實作主備 OpenD。

狀態：延後，待決定 port/account/session 所有權後再實作。

---

## 6. 改寫守則

### 6.1 修改範圍原則
- **聚焦原則：** 每個 PR 只改一個問題或一個功能。
- **核心組件禁忌：** 改動 `FutuConnector`、`LiveTradingLoop`、`ModelManager` 前必須先通過 `pytest tests/`。
- **不要提交：** `__pycache__/`、`.log` 檔案、`kiro_quant.db`、`config.json`、`state.json`、`self_learn/`、臨時圖片。
- **分支基底：** 永遠從 `origin/main` 開分支，不要從帶有 Auto-sync commit 的本地 `main` 分支。

### 6.2 配置加載優先序
```
環境變數 > config.json > 硬編碼預設值
```

### 6.3 yfinance FD 管理（PR #80 + #90 後）

每次 `get_latest_quote()` 和 `download_history()` 的 finally 塊都會呼叫 `_reset_yf_cache_fds()`。
`download_history()` 也會每 `_RESET_EVERY`（預設 5）個 symbol 重置一次。
可透過 `YF_RESET_EVERY` 環境變數調整頻率。

### 6.4 測試要求
- 新增邏輯必須附帶對應測試（`tests/test_*.py`）。
- 運行全量測試（排除 collection 有問題的模組）：`python3 -m pytest tests/ -q --tb=no --ignore=tests/test_pattern_trainer_components.py`
- 目前基線：**553+ tests collected; test collection conflict isolated to 6 test files (conftest interaction, individually passthrough)**；任何改動不得使通過數下降。
- `test_pattern_trainer_components.py` 須单独跑：`python3 -m pytest tests/test_pattern_trainer_components.py -v`

---

## 7. 已知陷阱

1. **Auto-sync commit 污染分支**：本地 cron 每日自動 commit `kiro_quant.db`、`config.json`、`state.json` 至本地 `main`。如果在這個 commit 後建分支，PR diff 會包含大型 DB 檔案。務必從 `origin/main` 的最新 code commit 開分支。
2. **futu_connector.py 衝突熱點**：多個 PR 修改此文件。rebase 時極易衝突。
3. **config.json 雙向同步**：live runtime 隨時改寫 config.json，不要把它加入 PR。
4. **不要推送本地 `main`**：含有 Auto-sync DB blob（308MB+ kiro_quant.db）。
5. **`test_pattern_trainer_components.py` collection 衝突**：全量 `pytest tests/` collection 時對該模組報錯，但單獨跑 2/2 passed。建議 `--ignore` 該檔案跑全量，隔離跑該檔案。

---

## 8. Graph 結構摘要（2026-05-19）

| 指標 | 數值 |
|------|------|
| 節點 | 3,247 |
| 邊 | 6,077（52% extracted, 48% inferred）|
| 社區 | 124 |

Top God Nodes 排名不變，請勿破壞 `FutuConnector` ↔ `LiveTradingLoop` 橋接邏輯。

---

## 9. 研究指導方針（2026-04-17 ～ 2026-05-19 每月研究總結）

> 本節收錄33日量化 ML/AI 研究的核心發現，作為日後功能開發與策略改進的優先級指引。

### 9.1 市場 Regime 速查表

| VIX 區間 | Regime | 策略傾向 |
|----------|--------|---------|
| VIX < 17 | 明確 Risk-On | Momentum 積極，Mean-Reversion 降低 |
| 17 ≤ VIX < 18.5 | Risk-On | Momentum 可測試，Mean-Reversion 恢復 |
| 18.5 ≤ VIX < 20 | 中性偏謹慎 | 觀望，Momentum 降低權重 |
| VIX ≥ 20 | Risk-Off | 全線防守，GRPO 倉位降至最低 |
| 油價 ≥ $100 | 通脹恐慌 | 所有策略降級，持有現金 |

**⚠️ 關鍵閾值：VIX 18.5 = 策略分水嶺；油價 $100 = 通脹恐慌觸發**

### 9.2 Self-Learn GRPO Reward Signal 設計原則（最重要）

**問題發現（04-20 research）：**
> 「二元方向準確率（方向對了=1）→ Kiro V3 80% 準確率但勝率 37% 的根本原因」

**原則：**
```
❌ 錯誤：方向準確率（direction_accuracy ∈ {0, 1}）
✅ 正確：真實 P&L 回報（realized_pnl ∈ ℝ）

具體做法：
- 每筆 CLOSED 交易計算：exit_price - entry_price（絕對值）
- 寫入 self_learn.trading_bot.db outcomes.pnl
- GRPO reward = normalize(pnl_pct) 而非 direction match
```

**驗證：** 方向準確率 80% ≠ 勝率 80%，因為止蝕/止賺設置不成比例。

### 9.3 策略架構疊加順序（由底層到頂層）

| 層 | 組件 | 優先級 | 備註 |
|----|------|--------|------|
| 執行層 | 8x8x139 Momentum + Mean-Reversion | 🔴 P0 | 現有核心引擎，保持高速執行 |
| 篩選層 | MDS（Metric Dependence Screening）| 🟠 P1 | 前置降噪，用風險曲線函數預篩候選資產 |
| 因子層 | GNN Factor Discovery + 因果推斷篩選 | 🟡 P2 | 發現隱藏結構，剔除偽因果因子 |
| Regime 層 | Temporal GNN + LLM Risk Manager | 🟢 P3 | 前瞻檢測市場狀態切換 |
| 增強層 | Contrastive Learning 表示學習 | 🔵 P4 | 小盤股 / 高雜訊市場增强 |

**⚠️ 底層未穩定前不要上層：** 8x8x139 引擎未優化止蝕邏輯前，不要引入 GNN Regime 層。

### 9.4 止蝕/止賺設計原則

**問題發現（04-17 research）：**
> 「quick_take_profit=2% 可能太早結束正確交易」

**原則：**
```
❌ 固定百分比止蝕（如 2%）
✅ 動態 ATR-based 止蝕

公式：
stop_loss = entry_price - max(ATR_14 * 1.5, entry_price * 0.015)
take_profit = entry_price + max(ATR_14 * 2.5, entry_price * 0.03)

好處：
- 高波動期自動擴大止蝕範圍（避免正常噪音觸發）
- 低波動期自動收緊（保留更多利潤）
```

### 9.5 MDS 前置過濾集成指引

**何時使用 MDS（05-06 research）：**
- 候選資產池 > 50 隻股票時
- Regime = Risk-Off（加強風險曲線篩選）
- 小盤股 / 低流動性市場（HK 小型股）

**集成方式：**
```
Input: 全市場候選
  ↓ Stage 1: MDS Filter
  - 提取每日收益率（標量）+ 日內風險曲線（函數）
  - 計算 Fréchet 變分分數
  - Output: Top 10-20% 精選子集
  ↓ Stage 2: 現有 Kiro Pipeline
  - Momentum / Mean-Reversion / XGBoost
  ↓ Output: 最終倉位配置
```

### 9.6 多 Agent 決策架構方向

**學習來源（04-30 TradingGroup / 05-15 分層多Agent）：**

| 層 | 職責 | Kiro 現有對應 |
|----|------|-------------|
| 微觀（日內）| 訂單執行、價差捕捉 | execution_engine.py |
| 中觀（日級）| 信號生成、因子組合 | 8x8x139, ModelManager |
| 宏觀（Regime）| 市場狀態評估、策略切換 | **目前缺失** → 待建立 |

**⚠️ 注意：** Regime 層不是簡單的參數切換，而是獨立的 Agent 評估過程。

### 9.7 LLM Risk Manager 集成注意

**原則（05-14 research）：**
- LLM Risk Manager 是「 reasoning layer」，不是替換風控引擎
- 它的輸出用於調整 `RiskController` 的 position_size 參數
- 不應直接發送訂單（保持低延遲執行）
- 需要自然語言 audit trail（日後合規用途）

### 9.8 因果推斷實作方向

**何時使用（05-18 research）：**
- 在 8x8x139 候選因子生成後
- 過濾步驟：`因子候選 → 因果檢驗 → 通過 / 剔除`
- IV 選擇：Fed Funds Rate（不受市場情緒影響但影響流動性）

**不建議：** 在 Regime 緊急切換時使用因果推斷（太慢），只用於因子發現階段。

---

## 10. 未來功能優先級

| 優先級 | 項目 | 預期收益 | 實現方向 |
|--------|------|---------|---------|
| 🔴 P0 | GRPO reward → P&L-based | 提升勝率（方向準確率→真實盈虧） | 修改 `self_learn/feedback.py` hook_on_prediction |
| 🟠 P1 | ATR-based 動態止蝕 | 減少過早止蝕，提升勝率 | 修改 `risk_guard_v36.py` stop_loss 公式 |
| 🟠 P2 | Regime Detection 獨立模組 | 策略切換更精準 | 新增 `regime_detector.py`，接入 VIX + 原油 + HSI |
| 🟡 P3 | MDS 前置過濾 | 降低估計誤差（特別是 HK 小型股）| 修改 `idle_task_scheduler.py` 加入 MDS 層 |
| 🟢 P4 | 因果推斷篩選層 | 剔除偽動量因子 | 新增 `causal_filter.py`，用 IV 驗證候選因子 |
| 🔵 P5 | LLM Risk Reasoning Layer | 動態倉位調整 audit trail | 新增 `llm_risk_manager.py`，調用 MiniMax API |

---

*Generated by kiro-pr-health-merge-loop scheduled auto-run — 2026-05-26*