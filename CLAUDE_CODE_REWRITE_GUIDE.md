# Claude Code Rewrite Guide — Kiro Quant V3

> 最後更新：2026-10-27 05:11 HKT（kiro-pr-health-merge-loop scheduled auto-run）
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
- MDS (Metric Dependence Screening) §9.5 Fréchet variation scoring

---

## 2. 核心抽象（God Nodes）

| 排名 | 組件 | 職責 |
|------|------|----------|
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

## 3. 目前健康狀態（2026-10-27）

| 檢查項 | 狀態 | 備註 |
|--------|------|------|
| 代碼圖譜 GRAPH_REPORT.md | ⚠️ 過期 | 檔案最後生成 2026-09-26（已 31 天）；dashboard `/api/kiro/snapshot` graph 欄位仍即時（4,548 nodes / 9,889 edges / 209 communities）|
| Dashboard 3000 | ✅ 開放 | next-server PID 31563，uptime from Oct24；`/kiro` 與 `/` 均 HTTP 200 |
| Node internal 8080 | ✅ 開放 | node-MainThread |
| Gateway 18789 | ✅ 開放 | PID 44698，/health → {"ok":true,"status":"live"} |
| WebSocket 8787 | ✅ 開放 | kiro-ws-server.mjs PID 31555 |
| v3_launcher.py | ✅ 運行中 | PID 31549，--dry-run，uptime from Oct24，logAgeSeconds 83 |
| OpenD 127.0.0.1:11112 | ✅ 可達 | FutuOpenD PID 31075，snapshot opend.reachable=true |
| kiro_quant.db | ✅ 正常 | -shm/-wal 存在，正被使用（WAL 15.1MB）|
| trades.jsonl | ✅ 正常 | 4,211 行 |
| state.json | ✅ 合法 | circuit_broken=false，consecutive_errors=0 |
| Runtime issues | ✅ 0 個 | launcherPids=[31549]，無 wrapper / external / duplicate |
| 回歸測試 | ⚠️ 有基線失敗 | 本地 main（ahead 44）：19 failed / 1,137 passed / 1 skipped；origin/main（PR base）：39 failed / 734 passed（既有基線，非本循環造成）|
| Account snapshot | ⚠️ 過期 | accountSnapshotAgeSeconds 77,237（stale）；totalAssets 886,135 / cash 892,561 / MV -6,426 / 1 position |
| 本地 kiro-quant-v3 main | ⚠️ ahead 44 | auto-sync commits（依規則不推 local main）|
| 開放 PRs | ✅ 0 個 | kiro-quant-v3 與 OpenStock 兩 repo GitHub API 均回 `[]`（2026-10-27 05:11 HKT 確認）|

**Runtime info（2026-10-27 05:11 HKT）：**
- openclaw gateway PID 44698（node v24.22.1），`/health` → `{"ok":true,"status":"live"}`
- next-server PID 31563，port 3000 (Next.js)，uptime from Oct24；`/kiro` HTTP 200
- kiro-ws-server.mjs PID 31555，port 8787
- v3_launcher.py --dry-run PID 31549，uptime from Oct24，`logs/v3_live.log` 最後活動 05:10 HKT
- OpenD（FutuOpenD PID 31075）127.0.0.1:11112 reachable
- 閒置模式運行正常：`[MARKET_IDLE] Running market analysis...` / `[IDLE_COLLECT] Syncing 69 symbols...`
- runtime 狀態欄位：`issues=[]`、`launcherWrapperPids=[]`、`launcherExternalPids=[]`（上一輪記錄嘅 external_v3_launcher stale 誤報已消失）
- 帳戶：auto_trade=true、paper_trading=false、market_mode=AUTO、posture=neutral、dailyTrades=21
- 本地 `main` 落後 `origin/main` 44 commits（auto-sync），依規則不推送

---

## 4. PR 記錄（完整至 2026-10-27）

### 已合併

| PR | 分支 | 合併日期 | 內容摘要 |
|----|------|----------|---------|
| #101 | `chore/pr100-clean-meta-labeling` | 2026-06-11 | 清理 meta-labeling workspace sync，準備發布 |
| #100 | `chore/workspace-sync-meta-labeling-20260611` | 2026-06-14 | feat: meta-labeling pipeline、HK prediction v2、trade quality gates + workspace sync |
| #99 | `docs/update-rewrite-guide-2026-05-31` | 2026-05-30 | 更新 rewrite guide — 2026-05-31 health loop |
| #98 | `fix/hk-trade-silence-phase1-2` | 2026-05-22 | 更新 rewrite guide 至 PRs #83-#91 |
| #97 | `fix/clean-runtime-state-positions` | 2026-05-20 | 清理 runtime state positions |
| #96 | `pr-95-fresh` | 2026-05-19 | feat: P3 §9.5 MDS Fréchet variation scoring in idle scheduler |
| #95 | `claude/implement-point-9-solution-C5Iag` | CLOSED 2026-05-19 | P3 §9.5 alternative branch，closed in favor of #96 |
| #94 | `claude/implement-point-9-solution-C5Iag` | 2026-05-19 | feat: P2 §9.1 Regime Detection — VIX×4 tiers + crude oil panic |
| #93 | `claude/implement-point-9-solution-C5Iag` | 2026-05-19 | feat: P0+P1 §9 GRPO P&L reward & ATR dynamic stop-loss |
| #92 | `docs/update-rewrite-guide-2026-05-17` | 2026-05-17 | 更新 in-repo rewrite guide 至 PR #91 |
| #91 | `fix/watchlist-version-field` | 2026-05-17 | screener 保留 version/tiers/metadata schema |
| #90 | `fix/yf-provider-aggressive-reset` | 2026-05-17 | 每 N 個 symbol 重置 tkr-tz.db FD |
| #89 | `fix/quote-na-sanitisation` | 2026-05-17 | `_coerce_price()` 拒 N/A/—/NaN/空串 |
| #88 | `fix/get-acc-list-no-kwargs` | 2026-05-17 | `discover_accounts()` 改用無 kwargs 的 `get_acc_list()` |
| #87 | `fix/db-manager-close-leak` | 2026-05-16 | DatabaseManager FD 洩漏：改用 `close()` 替代 GC 回收 |
| #86 | `feat/livedbpersist-inode-reconnect` | 2026-05-16 | LiveDbPersist inode 變更自動重連 |
| #85 | `feat/expand-universe-300` | 2026-05-16 | 擴展至 300+ 標的，三層 tier 管理 |
| #84 | `feat/db-persist-live-data` | 2026-05-16 | async SQLite 即時資料持久化（LiveDbPersist） |
| #83 | `feat/idle-kline-db-backfill` | 2026-05-16 | 閒置時 kline 資料回填 |
| #82 | `feat/kelly-position-sizing` | 2026-05-14 | Kelly 公式替換置信度倉位計算 |
| #81 | `feat/buying-power-preflight-guard` | 2026-05-14 | BuyingPowerGuard 預飛行檢查 |
| #80 | `fix/yf-provider-fd-regression` | 2026-05-14 | 關閉 yfinance Peewee SQLite 連線，回收 FD |
| #71 | `claude/busy-lamport-56e3c7` | 2026-05-15 | docs 更新（rewrite guide） |
| #58 | `feat/system-hardening-and-safety-rails` | CLOSED 2026-05-16 | DB blob 無法合併，safety-rail 已被 #80/#81/#82 覆蓋 |

### 開放 PR（2026-10-27）

| 狀態 | 數量 | PRs | 說明 |
|------|------|-----|---------|
| CANDIDATE | 0 | — | 無（GitHub API `[]`）|
| BLOCKED | 0 | — | 無 |
| SKIP | 0 | — | 無 |

> gh CLI 已認證（account: christophert0607-creator），PR 狀態以 GitHub API 為準（2026-10-27 05:11 HKT 重新確認 `[]`）。
> 合併閘門（Merge Gate）：本循環無候選 PR，無需 worktree 隔離測試，無 merge commit 產生。
> 2026-10-13 記錄嘅 8 個 open PR 已全部結案：#93/#94/#96/#97/#98/#99/#100/#101 已 MERGED，#95 已 CLOSED。

---

## 5. 開放分支狀態（2026-10-27，無變動）

> 以下為本地 git 分支落後分析（origin/main 為基準）

| 分支 | ahead/behind | 狀態 | 備註 |
|------|-------------|------|------|
| `feature/v3.6-strategy-upgrade` | +123 / 0 | SKIP | 內容龐大（123 commits），需完整 review 才能合併 |
| `fix/optimization-parameters` | +178 / +15 | SKIP | 雙向落後，複雜 rebase |
| `feature/issue-25-gpu-expansion` | +178 / +5 | SKIP | 落後 main，落後 5 |
| `fix/model-loading-registry` | +105 / +1 | SKIP | 落後 main，落後 1 |
| `fix/futu-connector-config-loading` | +103 / 0 | SKIP | 103 commits ahead，需 review |
| `fix/live-execution-state-machine` | +56 / 0 | SKIP | 56 commits ahead |
| `fix/state-store-single-entrypoint` | +56 / 0 | SKIP | 56 commits ahead |
| `refactor/market-context-source-of-truth` | +56 / 0 | SKIP | 56 commits ahead |
| `refactor/risk-decision-contract` | +56 / 0 | SKIP | 56 commits ahead |
| `feature/phase1-data-unification` | WIP | SKIP | WIP branch，落後 main |
| `pr53` | +127 / 0 | SKIP | 127 commits ahead |
| `pr55` | +125 / 0 | SKIP | 125 commits ahead |
| `fix/circuit-breaker-zero-equity` | +68 / 0 | SKIP | 68 commits ahead |

---

## 6. 開放工作（backlog）

### ⚠️ 手動操作（不需要新分支）

1. **回歸基線修復（本次循環發現，最高優先）** — 全量 `pytest tests/` 有 19 個失敗（見 §7.4），涉及 Kelly 倉位、model_registry sidecar、multi-market 路由、signal density 四組；建議逐組開分支修復。
2. **REAL 帳戶** — broker 端問題，提 Futu 支援票，無需程式碼分支。
3. **Account snapshot 過期** — `accountSnapshotAgeSeconds` 77,237 秒，snapshot cron（`kiro-v3-account-assets`）上次成功為 2026-10-26 07:45，需檢查其排程是否仍然生效。
4. ~~gh auth refresh~~ — 已解決：`gh` 已認證（account `christophert0607-creator`，scopes 含 `repo`），PR 狀態以 GitHub API 為準。
5. **PR #95 merge conflict** — 已結案（PR 已 CLOSED，內容由 #96 承繼），毋須再處理。
6. **graphify-out 過期** — `GRAPH_REPORT.md` 停留於 2026-09-26；dashboard graph API 仍即時，若要更新靜態報告需重跑 graphify。

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

## 7. 改寫守則

### 7.1 修改範圍原則
- **聚焦原則：** 每個 PR 只改一個問題或一個功能。
- **核心組件禁忌：** 改動 `FutuConnector`、`LiveTradingLoop`、`ModelManager` 前必須先通過 `pytest tests/`。
- **不要提交：** `__pycache__/`、`.log` 檔案、`kiro_quant.db`、`config.json`、`state.json`、`self_learn/`、臨時圖片。
- **分支基底：** 永遠從 `origin/main` 開分支，不要從帶有 Auto-sync commit 的本地 `main` 分支。

### 7.2 配置加載優先序
```
環境變數 > config.json > 硬編碼預設值
```

### 7.3 yfinance FD 管理（PR #80 + #90 後）

每次 `get_latest_quote()` 和 `download_history()` 的 finally 塊都會呼叫 `_reset_yf_cache_fds()`。
`download_history()` 也會每 `_RESET_EVERY`（預設 5）個 symbol 重置一次。
可透過 `YF_RESET_EVERY` 環境變數調整頻率。

### 7.4 測試要求
- 新增邏輯必須附帶對應測試（`tests/test_*.py`）。
- 運行全量測試（排除 collection 有問題的模組）：
  ```bash
  python3 -m pytest tests/ -q -p no:randomly \
    --ignore=tests/test_pattern_trainer_components.py \
    --ignore=tests/test_trade_outcome_features.py
  ```
- **基線 A — 本地 `main`（`3607c34`，ahead origin/main 44 commits，2026-10-27 05:11 HKT 實測）：**
  **19 failed, 1,137 passed, 1 skipped**（ignore 2 個 collection error 模組）。任何改動不得使 1,137 通過數下降。
  - 失敗：`test_kelly_position_sizing.py`(3)、`test_model_registry.py`(4)、`test_multi_market_isolation.py`(3)、`test_signal_density.py`(9)
- **基線 B — `origin/main`（`1db39d2`，PR 開分支基準，2026-10-27 05:30 HKT 實測）：**
  **39 failed, 734 passed, 1 skipped**（ignore 3 個 collection error 模組）。
  - 失敗：`test_meta_labeler.py`(10)、`test_signal_density.py`(9)、`test_model_registry.py`(4)、`test_yf_provider.py`(3)、`test_multi_market_isolation.py`(3)、`test_kelly_position_sizing.py`(3)、`test_main_loop_trade_bridge.py`(2)、`test_live_execution_ordering.py`(2)、`test_futu_connector_unlock.py`(2)、`test_market_context_sync.py`(1)
  - ⚠️ origin/main 測試檔較少（70 vs 本地 88），缺少本地新增嘅回歸測試，所以失敗數較高。**比較 PR 前後差異時，必須用同一個 base 比較**，唔好跨 base 比 absolute 數字。
- Collection error 模組（單獨跑 OK，全量跑報 ImportError）：
  - `tests/test_pattern_trainer_components.py`（`StockPatternModel`）— 單獨跑 2/2 passed
  - `tests/test_trade_outcome_features.py`（`AttentiveKiroLSTM`）
- 註：`test_signal_density.py` 現在 collection 正常但有 9 個 assertion 失敗，唔好再用 `--ignore` 掩蓋，應當作待修 bug 處理。
- **`test_meta_labeler_integration.py` collection error**：只在 `origin/main` base 出現（`should_take_trade` ImportError），本地 main 冇呢個問題；PR base 跑全量時需要多 `--ignore` 呢個檔。

---

## 8. 已知陷阱

1. **Auto-sync commit 污染分支**：本地 cron 每日自動 commit `kiro_quant.db`、`config.json`、`state.json` 至本地 `main`。如果在這個 commit 後建分支，PR diff 會包含大型 DB 檔案。務必從 `origin/main` 的最新 code commit 開分支。
2. **futu_connector.py 衝突熱點**：多個 PR 修改此文件。rebase 時極易衝突。
3. **config.json 雙向同步**：live runtime 隨時改寫 config.json，不要把它加入 PR。
4. **不要推送本地 `main`**：含有 Auto-sync DB blob（308MB+ kiro_quant.db）。
5. **`test_pattern_trainer_components.py` collection 衝突**：全量 `pytest tests/` collection 時對該模組報錯，但單獨跑 2/2 passed。建議 `--ignore` 該檔案跑全量，隔離跑該檔案。
6. **`test_trade_outcome_features.py` collection 衝突**：同上 `ImportError: AttentiveKiroLSTM`，建議 `--ignore`。
7. **`test_signal_density.py`**：collection 已正常，但 9 個 assertion 失敗（swing/model BUY 抑制、sell 延後、throttle、排序去偏）。屬實質回歸 bug，唔應以 `--ignore` 處理。
8. **`test_kelly_position_sizing.py` / `test_model_registry.py` / `test_multi_market_isolation.py`**：共 10 個失敗，屬既有基線問題（Kelly zero-edge 阻擋、checkpoint sidecar 驗證、HK 帳戶路由）。
9. **`test_meta_labeler_integration.py` collection 衝突**（PR #101）：ImportError: `should_take_trade` 不存在於 `self_learn.meta_labeler`。
10. **AttentiveKiroLSTM.forward() 簽名**：移除 `return_hidden` 參數會破壞 `test_model_registry.py` 測試。修改時需保持向後相容。

---

## 9. Graph 結構摘要（2026-10-27）

| 指標 | 數值 |
|------|------|
| 節點 | 4,548 |
| 邊 | 9,889 |
| 社區 | 209 |

數據來源：dashboard `/api/kiro/snapshot` → `graph` 欄位（coreNodes 首位 `LiveTradingLoop` degree 256、`FutuConnector` degree 207、health=ok、headline="OpenD connected"）。

⚠️ 靜態檔 `graphify-out/GRAPH_REPORT.md` 仍停留於 2026-09-26（31 天未更新），`graphify-data/` 目錄不存在；runtime graph API 數據正常。

Top God Nodes 排名不變，請勿破壞 `FutuConnector` ↔ `LiveTradingLoop` 橋接邏輯。

---

## 10. 研究指導方針（2026-04-17 ～ 2026-05-19 每月研究總結）

> 本節收錄33日量化 ML/AI 研究的核心發現，作為日後功能開發與策略改進的優先級指引。

### 10.1 市場 Regime 速查表

| VIX 區間 | Regime | 策略傾向 |
|----------|--------|---------|
| VIX < 17 | 明確 Risk-On | Momentum 積極，Mean-Reversion 降低 |
| 17 ≤ VIX < 18.5 | Risk-On | Momentum 可測試，Mean-Reversion 恢復 |
| 18.5 ≤ VIX < 20 | 中性偏謹慎 | 觀望，Momentum 降低權重 |
| VIX ≥ 20 | Risk-Off | 全線防守，GRPO 倉位降至最低 |
| 油價 ≥ $100 | 通脹恐慌 | 所有策略降級，持有現金 |

**⚠️ 關鍵閾值：VIX 18.5 = 策略分水嶺；油價 $100 = 通脹恐慌觸發**

### 10.2 Self-Learn GRPO Reward Signal 設計原則（最重要）

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

### 10.3 策略架構疊加順序（由底層到頂層）

| 層 | 組件 | 優先級 | 備註 |
|----|------|--------|---------|
| 執行層 | 8x8x139 Momentum + Mean-Reversion | 🔴 P0 | 現有核心引擎，保持高速執行 |
| 篩選層 | MDS（Metric Dependence Screening）| 🟠 P1 | 前置降噪，用風險曲線函數預篩候選資產 |
| 因子層 | GNN Factor Discovery + 因果推斷篩選 | 🟡 P2 | 發現隱藏結構，剔除偽因果因子 |
| Regime 層 | Temporal GNN + LLM Risk Manager | 🟢 P3 | 前瞻檢測市場狀態切換 |
| 增強層 | Contrastive Learning 表示學習 | 🔵 P4 | 小盤股 / 高雜訊市場增强 |

**⚠️ 底層未穩定前不要上層：** 8x8x139 引擎未優化止蝕邏輯前，不要引入 GNN Regime 層。

### 10.4 止蝕/止賺設計原則

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

### 10.5 MDS 前置過濾集成指引

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

### 10.6 多 Agent 決策架構方向

**學習來源（04-30 TradingGroup / 05-15 分層多Agent）：**

| 層 | 職責 | Kiro 現有對應 |
|----|------|-------------|
| 微觀（日內）| 訂單執行、價差捕捉 | execution_engine.py |
| 中觀（日級）| 信號生成、因子組合 | 8x8x139, ModelManager |
| 宏觀（Regime）| 市場狀態評估、策略切換 | **目前缺失** → 待建立 |

**⚠️ 注意：** Regime 層不是簡單的參數切換，而是獨立的 Agent 評估過程。

### 10.7 LLM Risk Manager 集成注意

**原則（05-14 research）：**
- LLM Risk Manager 是「 reasoning layer」，不是替換風控引擎
- 它的輸出用於調整 `RiskController` 的 position_size 參數
- 不應直接發送訂單（保持低延遲執行）
- 需要自然語言 audit trail（日後合規用途）

### 10.8 因果推斷實作方向

**何時使用（05-18 research）：**
- 在 8x8x139 候選因子生成後
- 過濾步驟：`因子候選 → 因果檢驗 → 通過 / 剔除`
- IV 選擇：Fed Funds Rate（不受市場情緒影響但影響流動性）

**不建議：** 在 Regime 緊急切換時使用因果推斷（太慢），只用於因子發現階段。

---

## 11. 未來功能優先級

| 優先級 | 項目 | 預期收益 | 實現方向 |
|--------|------|---------|---------|
| 🔴 P0 | GRPO reward → P&L-based | 提升勝率（方向準確率→真實盈虧） | 修改 `self_learn/feedback.py` hook_on_prediction |
| 🟠 P1 | ATR-based 動態止蝕 | 減少過早止蝕，提升勝率 | 修改 `risk_guard_v36.py` stop_loss 公式 |
| 🟠 P2 | Regime Detection 獨立模組 | 策略切換更精準 | 新增 `regime_detector.py`，接入 VIX + 原油 + HSI |
| 🟡 P3 | MDS 前置過濾 | 降低估計誤差（特別是 HK 小型股）| 修改 `idle_task_scheduler.py` 加入 MDS 層 |
| 🟢 P4 | 因果推斷篩選層 | 剔除偽動量因子 | 新增 `causal_filter.py`，用 IV 驗證候選因子 |
| 🔵 P5 | LLM Risk Reasoning Layer | 動態倉位調整 audit trail | 新增 `llm_risk_manager.py`，調用 MiniMax API |

---

*Generated by kiro-pr-health-merge-loop scheduled auto-run — 2026-10-27 05:11 HKT*
