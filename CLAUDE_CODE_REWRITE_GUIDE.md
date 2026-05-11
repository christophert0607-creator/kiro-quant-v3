# Claude Code Rewrite Guide — Kiro Quant V3

> 最後更新：2026-05-11 05:01 CST（由 kiro-pr-health-merge-loop cron 自動執行）

---

## 1. 項目概覽

**Kiro Quant V3** 是一個基於 Python 的量化交易系統，核心能力包括：
- 富途 OpenAPI (FutuConnector) 即時報價與交易
- LSTM / AlphaEngine 模型驅動的趨勢預測
- 技術指標生成與風險控制
- 回測引擎與即時交易循環

---

## 2. 核心抽象（God Nodes）

根據最新 Graph Report（2026-05-11，208 文件 · 2767 節點 · 5001 邊 · 119 communities）：

| 排名 | 組件 | 邊數 | 職責 |
|------|------|------|------|
| 1 | `FutuConnector` | 149 | OpenAPI 連接、報價緩存、重連邏輯 |
| 2 | `LiveTradingLoop` | 104 | 主交易循環、狀態機、訂單執行 |
| 3 | `FutuConfig` | 81 | 連線配置（host/port/trd_env） |
| 4 | `TechnicalIndicatorGenerator` | 81 | 技術指標計算 |
| 5 | `LiveConfig` | 55 | 運行時配置熱更新 |
| 6 | `RiskController` | 54 | 倉位與風險限制 |
| 7 | `ModelManager` | 47 | 模型加載、註冊表、自動重建 |
| 8 | `DataPreparer` | 45 | 數據預處理與特徵工程 |

**重要：** 任何改動上述組件都應該經過完整回歸測試（`pytest tests/`）。

---

## 3. 目前健康狀態（2026-05-11 05:01 CST）

| 檢查項 | 狀態 | 備註 |
|--------|------|------|
| 代碼圖譜 | ✅ 健康 | 208 文件，2767 節點，5001 邊，119 communities |
| 運行進程 | ✅ v3_launcher.py PID 510756 運行中（May 10 啟動） | 已運行 ~8.5 hrs |
| 服務端口 | ✅ 3000 (Next.js) / 3001 / 8080 (OpenClaw) 正常監聽 | |
| 數據文件 | ✅ 健康 | trades.jsonl 75KB / state.json 8.8KB / config.json 2.8KB / v3_live.log 3.3MB / decisions.jsonl 9.7MB |
| Crash 計數 | ⚠️ 1 | `.crash_count` 存在，需留意 runtime 穩定性 |
| 回歸測試 | ⚠️ 部分通過 | PR #58 分支：4 passed（preflight+db_manager），1 error（test_auto_trade_disabled import 缺失） |
| 當前分支 | ⚠️ `feat/pr58-safety-rails` @ `0665730`（ahead 1，大量 uncommitted live trading 變更） | |

---

## 4. PR Merge Loop 結果（更新至 2026-05-11）

### Open PRs（本次檢視）

| PR | 標題 | 分類 | Commit | 狀態 | 原因 |
|----|------|------|--------|------|------|
| #58 | Harden Kiro Quant runtime and add safety rails | 🔴 blocked | `33a7e8f` | open，mergeable=false，mergeable_state=dirty | 與 main 有 6 處 merge conflict（.gitignore、README.md、config.json、config.py、v3_launcher.py、main_loop.py） |

### 已合併 / 已關閉（歷史）

| PR | 標題 | 處理方式 | Commit |
|----|------|----------|--------|
| #69 | fix: add KIRO_LOG_DIR env var to isolate test logs from production | ✅ 已合併 | `428f165` |
| #68 | fix: per-symbol timeout handling + wall-clock timeout for Futu SDK connect | ✅ 已合併 | `666593e` |
| #62 | Fix FutuConnector ignoring host/port/trd_env from config.json | ✅ 已合併 | `d812a5f` |
| #63 | fix: QuoteCache - stop crash after [SCREENER] log | 🔒 已關閉 | `f434292` |
| #54 | Enforce bounded Futu reconnect budget | 🔒 已關閉 | `18f2674` |
| #61 | Fix model loading with registry-based resolution | 🔒 已關閉 | `31d3543` |
| #60 | Fix/optimization parameters | 🚫 已關閉 | — |

### 本次測試細節

- **Worktree 測試**：於 `/tmp/kiro-pr58-test-1778447056` 以 detached HEAD `33a7e8f` 運行 `pytest tests/`。
- **通過測試**：
  - `test_preflight_phase3.py` × 2 passed
  - `test_db_manager_phase2.py` × 2 passed
- **失敗測試**：
  - `test_auto_trade_disabled.py`：collection error，`ModuleNotFoundError: No module named 'tests.test_main_loop_state_restore'`
- **Merge 測試**：於 `/tmp/kiro-merge-test-1778447036` 嘗試將 `origin/feat/system-hardening-and-safety-rails` 合入 `main`（`428f165`），結果自動合併失敗，衝突文件共 6 個。

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
- 目前 runtime 基線：**408 passed, 101 warnings**；PR58 candidate + latest main + stub fix 基線：**419 passed, 101 warnings**；任何改動不得使通過數下降。
- 備註：本機 `python3` 目前指向 Python 3.14，且 pytest capture 會觸發 `FileNotFoundError`；測試應使用 CI 對齊的 Python 3.12 並加 `-s`。

---

## 6. 常見陷阱

1. **futu_connector.py 衝突熱點：** PR #62、#63、#54 都曾修改此文件。在最新 `main` 上，它已同時包含 `ConnectionState`、`QuoteCache` 與配置加載邏輯，rebase 時極易衝突。
2. **config.json 雙向同步：** `main` 上的 `config.json` 已經歷多輪擴展（MarketContext、idle scheduler、trd_env），舊分支的 config 變更幾乎必然衝突。
3. **模型註冊表漂移：** `v3_pipeline/models/registry.py` 與 `models_registry.json` 已於 Phase 3/4/5 引入，舊 PR 若也改模型加載，需確認是否已涵蓋。
4. **測試 stub 污染：** 測試若向 `sys.modules` 注入假 module，必須在匯入目標後清理，否則會污染後續測試收集。

### 6.1 函數簽名 / 調用站點同步陷阱（🚨 已實際造成崩潰）

**問題本質：** `RiskController.allow_daily_loss()` 在 `manager.py` 已改為需要 `day_start_equity` 和 `current_equity` 兩個參數，但 `main_loop.py:1669` 的調用站點依然以無參數方式調用，導致 `TypeError: missing 2 required positional arguments`。

**受影響檔案：**
- `v3_pipeline/risk/manager.py` — 函數定義（第 139-155 行）：需要 `day_start_equity: float` + `current_equity: float`
- `v3_pipeline/core/main_loop.py` — 調用站點（第 1669 行）：`self.risk_controller.allow_daily_loss()`（無參數）

**正確調用方式（需傳入當日開倉 equity 和當前 equity）：**
```python
# main_loop.py line 1669 應改為：
day_equity = getattr(self, "day_start_equity", None)
curr_equity = self.market_contexts.get("equity", 0) if hasattr(self, "market_contexts") else 0
if day_equity and curr_equity and hasattr(self.risk_controller, "allow_daily_loss") and not self.risk_controller.allow_daily_loss(day_equity, curr_equity):
    self.logger.warning("[DAILY_LOSS_GATE][%s] blocked BUY: daily loss limit reached", symbol)
    return
```

**防範守則：**
- 修改函數簽名時，**必須同步搜尋並更新所有調用站點**（`grep -rn "func_name(" --include="*.py"`）
- 優先使用 IDE 的 "Find References" / "Go to Definition" 功能確認所有調用點
- 若函數有可選參數，確保調用方明確傳入或函數本身有合理預設值；從無參數改為有參數是**破壞性變更**，等同樣需要更新所有調用方
- **每次重構後立即運行 `pytest tests/`** 確認沒有因簽名變更導致的崩溃

---

## 7. 待辦（Next Action）

- [x] **DB fetch-sync 任務**：將 `/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/kiro_quant.db` 接入 fetch 數據流程；每次成功 fetch 行情 / OHLCV 後同步 upsert 到 `market_data`，K 線會補 RSI/MACD/Bollinger 等技術特徵，失敗時不得阻塞交易循環，只記錄 warning。
- [x] **IDLE 全市數據輪轉補齊**：新增 universe file loader、rotation cursor、每次 idle session 上限、history period/interval 設定、DB freshness skip；預設先用 seed universe，換成全市 symbol 檔後即可分批補齊。
- [x] **處理 PR #58 safety rails 本地整合**：當前分支 `feat/pr58-safety-rails` 已在 `local HEAD`。
- [x] 修復 `test_idle_collect` stub 污染並通過全量測試。
- [ ] **🔴 PR #58 解衝突**：`feat/system-hardening-and-safety-rails`（`33a7e8f`）與 `main`（`428f165`）存在 6 處 merge conflict，必須 rebase/merge 解決後才能進入 merge gate。
- [ ] **🔴 修復 `test_auto_trade_disabled.py`**：該測試 import `tests.test_main_loop_state_restore`，但該模組不存在；需補上 stub 或修正 import 路徑。
- [ ] **⚠️ 處理 live trading uncommitted 變更**：當前 `feat/pr58-safety-rails` 工作區有大量未提交變更（config.json、state.json、symbol lists、DB、v3_launcher.py 等），這些是 runtime 運作產生的 live 狀態，不應混入 PR branch；需 stash / 另開 branch 隔離。
- [ ] **⚠️ 跟進 `.crash_count = 1`**：runtime 曾 crash 一次，需從 `v3_live.log` 與 `idle_scheduler.log` 排查原因。
- [ ] **⚠️ 清理舊 worktrees**：`/tmp/kiro-pr-58-worktree`、`/tmp/kiro-pr58-candidate`、`/tmp/kiro-pr58-check`、`/tmp/kiro-pr62` ~ `/tmp/kiro-pr70` 多為 detached HEAD，已標記 prunable，應清理以釋放空間。
- [ ] 下次 PR Health Merge Loop 預計運行時間：每日 07:45 CST。

---

*Maintained by kiro-pr-health-merge-loop cron / manual Claude-compatible health runs.*

---

## 8. 下一階段升級藍圖（2026-05-11 起）

> 基於 2026-04-17 至 2026-05-11 每日研究整合，涵蓋 Temporal GNN、GRPO、Multi-Agent、MDS、Regime Detection 等前沿概念與 Kiro V3 的落地可行性分析。

### 8.1 現有系統評估（2026-05-11 快照）

| 指標 | 數值 | 評估 |
|------|------|------|
| 當前模式 | HK_lite | ⚠️ 僅运行香港市場 |
| 累計 P&L | -$3,219 | ❌ 虧損中 |
| 日內交易筆數 | 462 筆/日 | 過度交易，信噪比低 |
| 模型方向準確率 | ~80% | ✅ |
| 勝率 | ~37% | ❌ 方向正確但止損/止賺設置有問題 |
| 持倉分散 | NFLX/MSTR/XLE/XLB/IDV | sectors 隨機，缺乏前置篩選 |

**三大核心問題：**

1. **Reward Signal 設計錯誤** — GRPO Self-Learn 用「方向對錯」而非「真實 P&L」作為 reward，導致模型學習了正確的方向但錯誤的盈虧。
2. **Regime Detection 缺位** — 系統在 VIX 19.50 和 VIX 17.83 時的交易行為完全相同，缺乏市場狀態感知。
3. **資產池無預篩選** — 42 隻 US 股票同時交易，大量噪聲倉位。

---

### 8.2 系統架構願景（Layer by Layer）

```text
Layer 0: 數據輸入
├── YFinance（日線/小時線）
└── Futu OpenAPI（即時報價）

Layer 1: Regime Detection ⭐ 新增（最優先）
├── VIX 閾值自動化切換（Phase 1-A，立即可落地）
├── Regime-Adaptive Position Sizing（Phase 1-B）
└── Temporal GNN Regime Detection（Phase 2，中期目標）

Layer 2: 資產預篩選 ⭐ 新增
├── MDS 簡單版：流動性 + 波幅雙濾（Phase 1）
└── MDS 完整版：Fréchet 變分風險曲線（Phase 2）

Layer 3: 因子發現與信號生成
├── XGBoost（139維特徵，8x8網格）→ 核心引擎
├── GNN Factor Discovery → 捕捉隱藏因子關係
├── Sentiment-Driven Overreaction → 日內過度反應
└── Agentic Factor Discovery → LLM 假設驗證

Layer 4: 策略引擎
├── Momentum（8x8x139）→ 順勢做多
├── Mean-Reversion → 震盪市輔助
├── GRPO Self-Learn（需大改 reward function）→ 自適應
└── Multi-Agent Trading（TradingGroup 啟發）→ 協作框架

Layer 5: 風險管理 ⭐ 需升級
├── 固定止損（2%）→ VIX-conditional 動態止損
├── 固定倉位 → Regime-Adaptive Position Sizing
└── CrabTrap 雙重審批機制（可選長期）

Layer 6: 執行
└── Futu OpenD / YFinance fallback
```

---

### 8.3 優先級矩陣

| 升級項目 | 影響 | 代價 | 優先級 |
|----------|------|------|--------|
| GRPO Reward → P&L based | 勝率 37% → 預計 50%+ | 極低 | 🔴 P0 |
| VIX Regime Switcher | 減少錯誤市場狀態下的交易 | 極低 | 🔴 P0 |
| Regime-Adaptive Position Sizing | 減少 VIX 高位時虧損 | 極低 | 🟠 P1 |
| MDS Pre-Filter（簡單版）| 降低噪聲倉位，提高 Sharpe | 低 | 🟠 P1 |
| Dynamic Stop-Loss（VIX-conditional）| 減少尾部虧損 | 低 | 🟡 P2 |
| Temporal GNN Regime Detection | 前瞻 Regime 預警 | 中等 GPU | 🟡 P2 |
| Multi-Agent 架構 | 提升信號質量 | 高 | 🟢 P3 |

---

### 8.4 P0 項目詳解

#### P0-A：GRPO Reward Function 大改

**現有問題：**
```python
# 錯誤的 reward 設計（當前）
reward = 1 if direction_correct else 0
# → 模型學會了「預測方向」但沒學會「預測盈虧」
# → 導致方向準確率 80% 但勝率 37%
```

**修復方案：**
```python
# 正確的 reward 設計
def grpo_reward(trade_record):
    pnl = trade_record.realized_pnl
    cost = trade_record.transaction_cost
    sharpe = trade_record.sharpe_contribution(lookback=20)
    return (pnl - cost) / max_abs_pnl  # normalized P&L-based reward
```

**落地代價：** 只需修改 reward function，不需要新數據源或基礎設施。

#### P0-B：VIX Regime Switcher

**Regime-Adaptive Position Sizing 系數表：**

| VIX 區間 | Regime 標籤 | Momentum 權重 | Mean-Rev 權重 | GRPO 倉位系數 |
|----------|--------------|----------------|---------------|---------------|
| VIX < 17 | Risk-On | 1.0x | 0.2x | 1.0x |
| VIX 17-18 | Neutral | 0.6x | 0.4x | 0.7x |
| VIX 18-25 | Risk-Off | 0.3x | 0.5x | 0.4x |
| VIX > 25 | Panic | 0.1x | 0.2x | 0.1x |

**實現代碼（3行概念）：**
```python
def get_regime_multiplier(vix: float) -> dict:
    if vix < 17:   return {"momentum": 1.0, "mean_rev": 0.2, "grpo": 1.0}
    if vix < 18:   return {"momentum": 0.6, "mean_rev": 0.4, "grpo": 0.7}
    if vix < 25:   return {"momentum": 0.3, "mean_rev": 0.5, "grpo": 0.4}
    return              {"momentum": 0.1, "mean_rev": 0.2, "grpo": 0.1}
```

---

### 8.5 下一階段行動計劃

**立即（今日可執行）：**
- [ ] 修改 GRPO reward function → 改為真實 P&L based reward
- [ ] 加入 VIX 閾值 regime switcher（3行代碼 + 系數表）
- [ ] 設定 Regime-Adaptive position sizing 系數表

**短期（1-2週）：**
- [ ] MDS 簡單版預篩選上線（流動性 > $5M 日均成交 + 30日波幅 < 80%）
- [ ] Dynamic stop-loss 改為 VIX-conditional

**中期（1-2個月）：**
- [ ] Temporal GNN 概念驗證（用歷史數據跑 batch）
- [ ] Multi-Agent 架構設計文檔
- [ ] Agentic Factor Discovery → LLM 假設驗證模組

**長期目標：**
- [ ] TradingGroup 風格 Multi-Agent 協作框架
- [ ] Temporal GNN 即時 Regime 預警引擎

---

### 8.6 研究沉澱：概念棧索引

| 概念 | 文件 | 與 V3 整合方式 |
|------|------|----------------|
| Temporal GNN Regime Detection | `wiki/concepts/temporal-gnn-regime-detection.md` | Regime 前瞻預警，調整策略權重 |
| GNN Factor Discovery | `wiki/concepts/graphs-factor-discovery.md` | 圖嵌入 → XGBoost 特徵輸入 |
| Agentic Factor Discovery | `wiki/concepts/agentic-factor-discovery.md` | LLM 假設生成 → 可審計因子研究 |
| Metric Dependence Screening | `wiki/concepts/metric-dependence-screening.md` | 前置資產預篩選，降低噪聲 |
| Sentiment-Driven Overreaction | `wiki/concepts/sentiment-driven-overreaction.md` | 日內動量領先指標 |
| Multi-Agent Trading | `wiki/concepts/multi-agent-trading.md` | TradingGroup 協作框架移植 |
| GRPO Self-Learn | `wiki/concepts/grpo-self-learn.md` | Reward signal 需改為 P&L based |
| CrabTrap Security | `wiki/concepts/crabtrap-llm-agent-security.md` | 風控雙重審批機制 |

---

*本節由 2026-05-11 Daily Research 整合研究寫入。下一階段升級方向已定，優先落地 P0（GRPO Reward + VIX Regime Switcher）。*
