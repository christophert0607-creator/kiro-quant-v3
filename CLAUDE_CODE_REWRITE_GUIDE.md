# Claude Code Rewrite Guide — Kiro Quant V3

> 最後更新：2026-05-19 HKT（kiro-pr-health-merge-loop scheduled auto-run）
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

## 3. 目前健康狀態（2026-05-19）

| 檢查項 | 狀態 | 備註 |
|--------|------|------|
| Launcher PID | ✅ 運行中 | PID 2907479，~5h uptime |
| OpenD 11112 | ✅ 開放 | |
| WebSocket 8787 | ✅ 開放 | |
| Dashboard 3000 | ✅ 開放（next-server） | |
| state.json / config.json | ✅ 合法 JSON | |
| 回歸測試（隔離） | ✅ **622 passed, 1 skipped** | `test_pattern_trainer_components.py` 與全量 collection 衝突，單獨跑 passthrough |
| 回歸測試（全量 collection） | ⚠️ `test_pattern_trainer_components.py` collection error | 單獨跑：2 passed；與其他測試混跑時 import 順序衝突 |
| yfinance FD | ✅ 已修復 | PR #87 + #90 已生效 |

---

## 4. PR 記錄（完整至 2026-05-19）

### 已合併

| PR | 分支 | 合併日期 | 內容摘要 |
|----|------|----------|---------|
| #91 | `fix/watchlist-version-field` | 2026-05-17 | screener 保留 version/tiers/metadata schema，修復 test_universe_expansion |
| #90 | `fix/yf-provider-aggressive-reset` | 2026-05-17 | 每 N 個 symbol（預設 5）在 download_history 迴圈內重置 tkr-tz.db FD |
| #89 | `fix/quote-na-sanitisation` | 2026-05-17 | `_coerce_price()` 拒 N/A/—/NaN/空串，防止 ValueError 洩漏至 order builder |
| #88 | `fix/get-acc-list-no-kwargs` | 2026-05-17 | `discover_accounts()` 改用無 kwargs 的 `get_acc_list()` |
| #87 | `fix/db-manager-close-leak` | 2026-05-16 | DatabaseManager FD 洩漏：改用 `close()` 替代 GC 回收 |
| #86 | `fix/livedbpersist-inode-reconnect` | 2026-05-16 | LiveDbPersist Auto-sync 後 inode 變更自動重連 |
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
- 目前基線：**622 passed, 1 skipped**；任何改動不得使通過數下降。
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

*Generated by kiro-pr-health-merge-loop scheduled auto-run — 2026-05-19*
