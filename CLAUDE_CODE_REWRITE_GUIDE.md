# Claude Code Rewrite Guide — Kiro Quant V3

> 最後更新：2026-05-09 18:44 CST（由 Codex 代 Claude 執行健康檢查後更新）

---

## 1. 項目概覽

**Kiro Quant V3** 是一個基於 Python 的量化交易系統，核心能力包括：
- 富途 OpenAPI (FutuConnector) 即時報價與交易
- LSTM / AlphaEngine 模型驅動的趨勢預測
- 技術指標生成與風險控制
- 回測引擎與即時交易循環

---

## 2. 核心抽象（God Nodes）

根據最新 Graph Report（2678 nodes · 4858 edges · 120 communities）：

| 排名 | 組件 | 邊數 | 職責 |
|------|------|------|------|
| 1 | `FutuConnector` | 156 | OpenAPI 連接、報價緩存、重連邏輯 |
| 2 | `LiveTradingLoop` | 101 | 主交易循環、狀態機、訂單執行 |
| 3 | `FutuConfig` | 86 | 連線配置（host/port/trd_env） |
| 4 | `TechnicalIndicatorGenerator` | 66 | 技術指標計算 |
| 5 | `LiveConfig` | 54 | 運行時配置熱更新 |
| 6 | `RiskController` | 54 | 倉位與風險限制 |
| 7 | `ModelManager` | 47 | 模型加載、註冊表、自動重建 |
| 8 | `DataPreparer` | 45 | 數據預處理與特徵工程 |

**重要：** 任何改動上述組件都應該經過完整回歸測試（`pytest tests/`）。

---

## 3. 目前健康狀態

| 檢查項 | 狀態 | 備註 |
|--------|------|------|
| 代碼圖譜 | ✅ 健康 | 198 文件，2678 節點，結構完整 |
| 服務端口 | ✅ 3000 (Next.js) / 8080 (OpenClaw) 正常監聽 |
| 回歸測試 | ✅ 405 passed, 101 warnings（runtime）；419 passed, 101 warnings（PR58 candidate + latest main + stub fix） |
| 當前分支 | ✅ `feat/pr58-safety-rails` @ `local HEAD`（本地已提交，推送被 GitHub HTTPS 認證阻擋） |
| Runtime FD | ⚠️ 需跟進 | `v3_launcher.py` PID 38186 約 1858 FDs；`py-yfinance/tkr-tz.db` / WAL 佔約 1806 |

---

## 4. PR Merge Loop 結果（更新至 2026-05-09）

### 已合併 / 已關閉

| PR | 標題 | 處理方式 | Commit |
|----|------|----------|--------|
| #62 | Fix FutuConnector ignoring host/port/trd_env from config.json | ✅ 已合併 | `d812a5f` |
| #63 | fix: QuoteCache - stop crash after [SCREENER] log | 🔒 已關閉（代碼已透過其他 PR 合併） | `f434292` |
| #54 | Enforce bounded Futu reconnect budget | 🔒 已關閉（代碼已透過 PR#53 合併） | `18f2674` |
| #61 | Fix model loading with registry-based resolution | 🔒 已關閉（代碼已於 Phase 3/4/5 合併） | `31d3543` |
| #60 | Fix/optimization parameters | 🚫 已關閉（含 __pycache__ / log，品質不合格） | — |
| #58 | Harden Kiro Quant runtime and add safety rails | 🔴 原 PR 仍 open/dirty，head=`feat/system-hardening-and-safety-rails` @ `33a7e8f` | 需替換或更新 |

### 本次手動健康檢查

- 完成 DB fetch-sync：`DatabaseManager.save_market_quote()` 現在可將 quote/OHLCV snapshot upsert 入 `market_data`；`DataManager.get_market_data()` 與 `LiveTradingLoop._run_symbol_cycle()` 成功 fetch 後會同步寫入 `/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/kiro_quant.db`。
- K 線 DB sync 會先透過 `TechnicalIndicatorGenerator` 補齊價格行為特徵，再寫入 `market_data`；目前包含 RSI、MACD、Bollinger Bands、均線、ATR/ADX/CCI/MFI/OBV/ROC/WILLR/VWAP/KDJ 等欄位。
- IDLE 時段 historical backfill 現在會把 yfinance K 線同步入同一個 `market_data` 表：每批 backfill 會正規化 OHLCV、補技術指標、再 upsert 入 DB；單一 symbol 同步失敗只記 warning，不中斷 idle scheduler。
- DB sync 失敗策略：只記錄 warning，不阻塞行情 fetch 或交易循環；pytest 執行期間預設不自動連 production DB，測試用注入 fake/temp DB。
- 修復 `tests/test_idle_collect.py` 的 module stub 污染：該測試匯入 `v3_launcher` 時暫時 stub `v3_pipeline.models.brain`，但未清理 `sys.modules`，導致 `tests/test_pattern_trainer_components.py` 無法匯入 `StockPatternModel`。
- 使用 `/usr/bin/python3.12 -m pytest tests/ -q --tb=short -s` 驗證全量測試通過。
- GitHub 目前只剩 open PR #58；REST API 顯示 `mergeable=false`, `mergeable_state=dirty`, `rebaseable=false`。
- 替代分支 `origin/feat/pr58-safety-rails @ 3cc2721` 已存在；在 `/tmp/kiro-pr58-candidate` 合入 `origin/main @ 549794f` 無衝突。
- 替代分支合入最新 main 後，必須帶上 `tests/test_idle_collect.py` stub 清理修補；帶修補後全量測試為 **419 passed, 101 warnings**。
- 本地已提交 stub 修補與 guide 更新為 `local HEAD`；`git push origin HEAD:feat/pr58-safety-rails` 因 GitHub HTTPS 未登入而失敗。

---

## 5. 改寫守則（Rewrite Rules）

### 5.1 修改範圍原則
- **聚焦原則：** 每個 PR 只改一個問題或一個功能。
- **核心組件禁忌：** 改動 `FutuConnector`、`LiveTradingLoop`、`ModelManager` 前必須先通過 `pytest tests/`。
- **不要提交：** `__pycache__/`、`.log` 檔案、臨時圖片、個人設定。

### 5.2 配置加載優先序（已固化於 main）
```
環境變數 > config.json > 硬編碼預設值
```
- `FUTU_OPEND_HOST` / `FUTU_OPEND_PORT` / `FUTU_TRD_ENV`
-  connector 啟動時會先讀 `config.json` 的 `futu.*` 區段，再以環境變數覆蓋

### 5.3 連線狀態機（已於 main）
```python
class ConnectionState(enum.Enum):
    CONNECTED    = "CONNECTED"
    DEGRADED     = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
```
任何涉及重連邏輯的改動都必須兼容此狀態機。

### 5.4 QuoteCache 接口（已於 main）
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
- 運行全量測試：`/usr/bin/python3.12 -m pytest tests/ -q --tb=short -s`
- 目前 runtime 基線：**405 passed, 101 warnings**；PR58 candidate + latest main + stub fix 基線：**419 passed, 101 warnings**；任何改動不得使通過數下降。
- 備註：本機 `python3` 目前指向 Python 3.14，且 pytest capture 會觸發 `FileNotFoundError`；測試應使用 CI 對齊的 Python 3.12 並加 `-s`。

---

## 6. 常見陷阱

1. **futu_connector.py 衝突熱點：** PR #62、#63、#54 都曾修改此文件。在最新 `main` 上，它已同時包含 `ConnectionState`、`QuoteCache` 與配置加載邏輯，rebase 時極易衝突。
2. **config.json 雙向同步：** `main` 上的 `config.json` 已經歷多輪擴展（MarketContext、idle scheduler、trd_env），舊分支的 config 變更幾乎必然衝突。
3. **模型註冊表漂移：** `v3_pipeline/models/registry.py` 與 `models_registry.json` 已於 Phase 3/4/5 引入，舊 PR 若也改模型加載，需確認是否已涵蓋。
4. **測試 stub 污染：** 測試若向 `sys.modules` 注入假 module，必須在匯入目標後清理，否則會污染後續測試收集。

---

## 7. 待辦（Next Action）

- [x] **DB fetch-sync 任務（本輪優先）**：將 `/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/kiro_quant.db` 接入 fetch 數據流程；每次成功 fetch 行情 / OHLCV 後同步 upsert 到 `market_data`，K 線會補 RSI/MACD/Bollinger 等技術特徵，失敗時不得阻塞交易循環，只記錄 warning。
- [x] **處理 PR #58 safety rails 本地整合**：當前分支 `feat/pr58-safety-rails` 已在 `local HEAD`。
- [x] 修復 `test_idle_collect` stub 污染並通過全量測試。
- [ ] 將 `test_idle_collect` stub 修補推送到 `origin/feat/pr58-safety-rails`，再以此分支替換舊 PR #58（或開 replacement PR）。
- [ ] 舊 PR #58 (`feat/system-hardening-and-safety-rails`) 不應直接 merge；它仍是 dirty/stale。
- [ ] 跟進 live runtime FD 增長：yfinance timezone cache sqlite descriptors 未釋放，雖未達 fd limit，但屬資源泄漏風險。
- [ ] 下次 PR Health Merge Loop 預計運行時間：每日 07:45 CST。

---

*Maintained by kiro-pr-health-merge-loop / manual Claude-compatible health runs.*
