# Claude Code Rewrite Guide — Kiro Quant V3

> 最後更新：2026-05-13 23:05 HKT（由 kiro-rewrite-loop 手動執行）

---

## 1. 項目概覽

**Kiro Quant V3** 是一個基於 Python 的量化交易系統，核心能力包括：
- 富途 OpenAPI (FutuConnector) 即時報價與交易
- LSTM / AlphaEngine 模型驅動的趨勢預測
- 技術指標生成與風險控制
- 回測引擎與即時交易循環

---

## 2. 核心抽象（God Nodes）

根據最新 Graph Report（338 nodes · 688 edges · 21 communities，v3_pipeline，2026-04-21）：

| 排名 | 組件 | 邊數 | 職責 |
|------|------|------|------|
| 1 | `FutuConnector` | 42 | OpenAPI 連接、報價緩存、重連邏輯 |
| 2 | `LiveTradingLoop` | 36 | 主交易循環、狀態機、訂單執行 |
| 3 | `TechnicalIndicatorGenerator` | 29 | 技術指標計算 |
| 4 | `ModelManager` | 29 | 模型加載、註冊表、自動重建 |
| 5 | `QuoteCache` | 27 | 集中式報價緩存 |
| 6 | `DataPreparer` | 25 | 數據預處理與特徵工程 |
| 7 | `HistoryPrimer` | 19 | 歷史數據預填充 |
| 8 | `RiskController` | 18 | 倉位與風險限制 |
| 9 | `StrategyFactory` | 16 | 策略工廠 |
| 10 | `HistoricalDataDownloader` | 16 | 歷史數據下載 |

**重要：** 任何改動上述組件都應該經過完整回歸測試（`pytest tests/`）。

---

## 3. 目前健康狀態

| 檢查項 | 狀態 | 備註 |
|--------|------|------|
| 代碼圖譜 | ✅ 最新 | GRAPH_REPORT 2026-05-13（2638 nodes, 4765 edges, 116 communities） |
| 服務端口 | ⚠️ OpenD 11112 正常；3000 / 8787 關閉 |
| 回歸測試（feat/pr58 @ f4203d9+fix） | ✅ 407 passed, 1 skipped | /tmp/kiro-pr58-new |
| 主分支 | ⚠️ `main`+`origin/main` @ `bdcf3b3`（正確 HEAD `695ad47` 在 stash 中）|
| state.json | ❌ UU 合并衝突，JSON 無效，runtime 無法保存狀態 |
| Dual launchers | ⚠️ PID 9970 (python3.14) + 12924 (python3, NO_FUTU_QUOTE=1) 同時運行 |

---

## 4. PR Merge Loop 結果（2026-05-13 23:05 HKT）

### 🔴 CRITICAL: origin/main 被錯誤重置（未修復）

**狀態：** `origin/main` + `local main` 仍在 `bdcf3b3`（Merge pull request #33）。正確 HEAD `695ad47` 保存在 git stash 中。

**修復方案（需管理員執行）：**
```bash
git checkout main
git reset --hard 695ad47
git push --force origin main
```

⚠️ 破壞性操作（force-push），需確認後執行。

---

### PR #58 — Harden Kiro Quant runtime and add safety rails

| 環境 | Commit | 結果 |
|------|--------|------|
| `/tmp/kiro-pr58-new` (worktree) | `f4203d9` + fix `060277c` | ✅ 407 passed, 1 skipped |

**本次修復：** `discover_accounts()` 兩個回歸（由 `0a8d0bd` per-market 重構引入）：
1. `_safe_trade_call("get_acc_list")` 的返回值 `data` 從未賦值給 `self.discovered_accounts`
2. TypeError fallback 只遍歷空的 `trade_ctxs`，忽略 legacy `trade_ctx` singleton

**推送阻塞：** `kiro_quant.db`（294MB）在 `0a8d0bd` 提交中超過 GitHub 100MB 限制。需先執行：
```bash
git filter-repo --path kiro_quant.db --invert-paths
git push --force origin feat/pr58-safety-rails
```

**gh CLI 未認證：** 無法從 GitHub API 確認 PR mergeable 狀態。

### 已合併 / 已關閉

| PR | 標題 | 處理方式 | Commit |
|----|------|----------|--------|
| #70 | dashboard-account-selector | ✅ 已合併 | `549794f` |
| #69 | fix: add KIRO_LOG_DIR env var | ✅ 已合併 | `428f165` |
| #68 | fix: circuit-breaker-zero-equity | ✅ 已合併 | `8731e66` |
| #67 | feat: phase6-7-observability-idle-scheduler | ✅ 已合併 | `6837bd3` |
| #62 | Fix FutuConnector ignoring host/port/trd_env | ✅ 已合併 | `d812a5f` |
| #60 | Fix/optimization parameters | 🚫 已關閉（品質不合格） | — |
| #25 | GPU long-term training expansion | ⏭️ SKIP | `gh` CLI 未認證 |

---

## 5. 改寫守則（Rewrite Rules）

### 5.1 修改範圍原則
- **聚焦原則：** 每個 PR 只改一個問題或一個功能。
- **核心組件禁忌：** 改動 `FutuConnector`、`LiveTradingLoop`、`ModelManager` 前必須先通過 `pytest tests/`。
- **不要提交：** `__pycache__/`、`.log` 檔案、臨時圖片、個人設定。

### 5.2 配置加載優先序（已固化於 main@695ad47）
```
環境變數 > config.json > 硬編碼預設值
```
- `FUTU_OPEND_HOST` / `FUTU_OPEND_PORT` / `FUTU_TRD_ENV`
- connector 啟動時會先讀 `config.json` 的 `futu.*` 區段，再以環境變數覆蓋

### 5.3 連線狀態機（已於 main）
```python
class ConnectionState(enum.Enum):
    CONNECTED    = "CONNECTED"
    DEGRADED     = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
```
任何涉及重連邏輯的改動都必須兼容此狀態機。

### 5.4 QuoteCache 接口（已於 main@695ad47）
```python
class QuoteCache:
    TTL_SECONDS: float = 30.0
    def __setitem__(self, symbol: str, value: Any) -> None: ...
    def __contains__(self, symbol: object) -> bool: ...
    def get(self, symbol: str, default: Any = None) -> Any: ...
    def get_missing(self, symbols: List[str]) -> List[str]: ...
    def refresh_batch_itick(self, symbols: List[str], market: str, *args: Any) -> dict: ...
```
`FutuConnector._quote_cache` 已由 plain `dict` 升級為 `QuoteCache`，改動時不要破壞 dict-compatible 接口。

### 5.5 測試要求
- 新增邏輯必須附帶對應測試（`tests/test_*.py`）。
- 運行全量測試：`python3 -m pytest tests/ -x -q --tb=short`
- 目前基線（worktree @ d576584）：**418 passed, 1 skipped**

---

## 6. 常見陷阱

1. **futu_connector.py 衝突熱點：** PR #62、#63、#54 都曾修改此文件。在正確的 `main`（695ad47）上，它已同時包含 `ConnectionState`、`QuoteCache` 與配置加載邏輯，rebase 時極易衝突。
2. **config.json 雙向同步：** `main` 上的 `config.json` 已經歷多輪擴展（MarketContext、idle scheduler、trd_env），舊分支的 config 變更幾乎必然衝突。
3. **origin/main 狀態監控：** 由於存在 force-push 風險，每次 merge loop 都需要確認 `origin/main` 是否處於正確狀態。

---

## 7. 待辦（Next Action）

- [x] **PR #58 測試**：worktree `/tmp/kiro-pr58-new` @ `f4203d9`+`060277c` — 407 passed, 1 skipped ✅
- [x] **Graph Report 更新**：2026-05-13 已更新（2638 nodes, 4765 edges）✅
- [ ] **🔴 緊急：解決 state.json 合并衝突** — runtime 每個 cycle 無法保存狀態
- [ ] **🔴 緊急：調查 dual launchers** — PID 9970 + 12924 同時運行，存在雙重交易風險
- [ ] **🔴 修復 origin/main**：`git reset --hard 695ad47 && git push --force origin main`（需管理員確認）
- [ ] **移除 kiro_quant.db 大文件**：`git filter-repo --path kiro_quant.db --invert-paths` 後才能推送 `feat/pr58-safety-rails`
- [ ] **PR #58 推送**：移除大文件後 push，然後在 GitHub 上 merge（需 `gh auth login` 或 web UI）
- [ ] **PR #25**（GPU expansion）需手動驗證或提供 `gh` 認證

---

*Generated by kiro-pr-health-merge-loop cron — do not edit manually unless you know what you're doing.*
