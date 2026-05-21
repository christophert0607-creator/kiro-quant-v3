# Kelly + Self-Learn 設計 audit

**日期**: 2026-05-20  
**範圍**: PR #82 (`feat/kelly-position-sizing`) + `self_learn/` 嘅 outcome closure pipeline  
**起因**: 港股時段（09:30–16:00 HKT）今日 0 個下單嘗試，844 次 `KELLY_ZERO_EDGE` skip。

---

## 1. 結論摘要

港股「唔識交易」**唔係**單一 bug，係**三層結構性問題疊加**：

| # | 問題 | 影響 | 嚴重 |
|---|---|---|---|
| A | Kelly 嘅 `win_rate / avg_win / avg_loss` 數據源 = **股票歷史 raw 日線價格 returns**，唔係策略 outcome | 港股熊市 universe 觸發 `zero_edge` → 全面禁買 (844 skip/日) | 🔴 設計級 |
| B | `self_learn` outcome closure pipeline **未接駁** — `hook_on_signal` 喺 swing/model BUY path 缺失，predictions=32466 / signals=5 (全部 test data) / outcomes=0 | 即使想用真實策略 outcome 修 A，亦冇數據可用 | 🔴 設計級 |
| C | 港股 lot size × 低 risk_pct → `alloc / price` < 1 lot → `_round_to_lot` 變 qty=0 | 即使 Kelly 過關，下唔到單(17 SKIP/日) | 🟠 sizing bug |

附加 finding: SHORT path 同 LONG path 對 `zero_edge` 處理唔一致（SHORT fallback、LONG return）。  
附加 finding: `self_learn/self_learn.db` 同 `self_learn/trading_bot.db` schema 分裂(兩個 DB 都有同名表)。

---

## 2. Issue A — Kelly 用咗 raw price returns

### 2.1 數據鏈

`v3_pipeline/core/main_loop.py` 三個 BUY/SHORT path 全部相同寫法：

```python
returns = self._get_buffer(symbol)["Close"].pct_change().dropna()    # 1300+ 條日線 raw return
mc = self.monte_carlo.stress_test(returns)                          # bootstrap
kelly = self._kelly_sizer.calculate_details(
    win_rate=mc["win_rate"], avg_win=mc["avg_win"], avg_loss=mc["avg_loss"],
)
```

| Path | 行號 | `zero_edge` 處理 |
|---|---|---|
| SHORT entry | 1199–1208 | fallback `confidence_to_risk_pct` (繼續) |
| LONG model BUY (主 path A) | 1335–1350 | **return** (skip) |
| Swing BUY | 1786–1803 | fallback `confidence_to_risk_pct` (繼續) |
| LONG model BUY (主 path B) | 1808–1859 | **return** (skip) |

### 2.2 `MonteCarloSimulator.stress_test` (`v3_pipeline/core/monte_carlo.py:21-49`)

```python
arr = pct_change_dropped.values                       # ~1300 raw daily returns
horizon = min(self.config.horizon, len(arr))           # default=20
for _ in range(1000):
    sampled = np.random.choice(arr, size=horizon, replace=True)
    sims.append(np.prod(1 + sampled) - 1)              # 20-day compound

win_rate  = (sim_arr > 0).mean()                       # 20日複合正收益概率
avg_win   = sim_arr[sim_arr > 0].mean()
avg_loss  = |sim_arr[sim_arr < 0].mean()|
```

**設計含義**：Kelly 衡量「**呢隻 stock 過去 5 年隨機抽 20 日嘅自然走勢**」，**完全冇睇 entry 條件、model confidence、swing signal、價格水平**。等於用「股票熊定牛」做唯一 entry filter。

### 2.3 實測 HK vs US 5y daily stats

| Symbol | win_rate (daily) | avg_win | avg_loss | E[r] daily | 5y total |
|---|---:|---:|---:|---:|---:|
| TSLA | 0.519 | 2.74% | 2.69% | +0.126‰ | +106.6% |
| AAPL | 0.532 | 1.23% | 1.22% | +0.083‰ | +134.8% |
| MSFT | 0.516 | 1.21% | 1.18% | +0.056‰ | +69.4% |
| SPY | 0.543 | 0.75% | 0.78% | +0.051‰ | +76.7% |
| 0700.HK | 0.475 | 1.85% | 1.65% | +0.013‰ | −18.9% |
| 9988.HK | 0.460 | 2.58% | 2.16% | +0.016‰ | −36.0% |
| 0939.HK | 0.508 | 1.10% | 1.05% | +0.039‰ | +41.7% |
| 0005.HK | 0.556 | 1.09% | 1.14% | +0.098‰ | +186.3% |
| **1024.HK** | 0.469 | 2.97% | 2.72% | **−0.052‰** | **−79.0%** |
| **3690.HK** | 0.462 | 2.74% | 2.41% | **−0.032‰** | **−69.7%** |

20-day bootstrap 會放大 drift bias — 負 E[r] 嘅 stock 喺 20 日 horizon 變成接近全跌:今日 log 顯示 0960.HK win_rate=0.371, 1109.HK=0.434, 1810.HK=0.428,全部低於 raw daily。

### 2.4 點解美股冇事

美股 universe 過去 5 年正 drift (TSLA/AAPL/MSFT/SPY raw daily E[r] 全部正)，bootstrap 20 日複合 win_rate ≥ 0.55，Kelly `b·p − q > 0` 永遠正 → 唔擋 BUY。

### 2.5 設計缺陷

| 缺陷 | 為何錯位 |
|---|---|
| 用 raw price returns 取代策略 outcome | Kelly 公式 require「策略條件下嘅 P(win) 同 win/loss magnitude」。raw returns 反映「市場 drift」而非「策略 edge」 |
| `horizon=20` 過長 | swing/day strategy 嘅 hold 多數係幾小時到幾日，20 日複合會稀釋短期 edge 並放大長期 drift |
| `np.random.choice(replace=True)` | bootstrap 假設 returns iid，忽略波幅 cluster + autocorrelation |
| MC 結果**先 zero_edge 後 sizing** | 即使 model 對 entry 有 high confidence，亦會被歷史市場 drift 否決 |

---

## 3. Issue B — self_learn outcome pipeline 斷咗

### 3.1 DB 狀態

```
self_learn/self_learn.db       predictions=0     signals=0   outcomes=0
self_learn/trading_bot.db      predictions=32466 signals=5   outcomes=0
```

- 兩個 DB schema 重複(`schema.py:DB_PATH=self_learn.db` vs `models.py:DB_PATH=trading_bot.db`)
- `trading_bot.db.signals` 5 條全部係 test data (`'test-prediction-id'`、`size=10/11/100`、價 `100/101/200/123.45/150.25`)
- `outcomes` 表 **0 條**

### 3.2 Pipeline 應該嘅走法

```
prediction → save_prediction()                    → predictions 表
  ↓ stored in self._pred_id_by_symbol[symbol]
BUY 成立 → hook_on_signal(prediction_id, ...)     → signals 表
  ↓ stored in self._signal_id_by_symbol[symbol]
SELL 成立 → on_trade_closed(signal_id, pnl, ...)  → outcomes 表 + signal.status=CLOSED
```

### 3.3 實際斷喺邊

```
main_loop.py:792  self._pred_id_by_symbol[symbol] = _pred_id_for_signal   # ✅ OK
main_loop.py:1531 from self_learn import hook_on_signal                  # ✅ 條 path 有
main_loop.py:1532   _sig_id = hook_on_signal(action="BUY", ...)
```

但 `hook_on_signal` **只喺一個 BUY path 入面 call**（line 1531，喺 `BUY_PLACED` decision trace 之後）。

**Swing BUY path (line 1786-1803)**:
```python
if buy_qty > 0:
    self._execute(symbol, "BUY", buy_qty, current_price, f"swing_signal_conf={confidence:.3f}")
    return                                  # ← 直接 return，冇 hook_on_signal
```

**Model signal BUY path (line 1808-1859)**:
```python
if buy_qty > 0:
    self._execute(symbol, "BUY", buy_qty, current_price, f"model_signal_conf={confidence:.3f}")
                                            # ← 冇 hook_on_signal
```

→ swing entry 同 model entry **都唔會寫 signals 表**。`_signal_id_by_symbol[symbol]` 永遠係空,所以 SELL 嗰陣 `on_trade_closed` line 2358 嘅 guard `if not sig_id or entry_price <= 0: return` 直接 abort,outcomes 永遠寫唔到。

### 3.4 連帶 dev/meta_labeling 確認

`dev/meta_labeling/DEVLOG.md:194`:
> Root Cause Confirmed: `hook_on_signal` in `feedback.py` not being called from `LiveTradingLoop`, OR `log_signal` called without `prediction_id`. The `prediction_id` lookup from `_pred_id_by_symbol` is correct in code, but appears to always return `None` at signal time.

→ 已知 issue,但 fix 仲未做。

---

## 4. Issue C — HK lot size × 低 risk_pct → qty=0

### 4.1 Sizing chain

```
risk_pct = confidence_to_risk_pct(confidence)         # 0.01 + 0.09*conf ∈ [0.01, 0.10]
alloc    = min(account*risk_pct, account*0.30)         # ≤ 30% account
buy_qty  = int(alloc / current_price)
_execute → qty = _round_to_lot(qty, symbol)            # 下取整到 lot
if qty <= 0: log "SKIP ... (lot rounding → qty=0)"
```

### 4.2 港股 lot 反例(以 $100k 模擬倉、conf=0.65 計)

| Symbol | px | lot | risk_pct | alloc | shares (pre-lot) | post-lot |
|---|---:|---:|---:|---:|---:|---:|
| 0939.HK | $8.85 | 1000 | 6.85% | $6,850 | 774 | 0 |
| 0005.HK | $138 | 400 | 6.85% | $6,850 | 49 | 0 |
| 1299.HK | $85 | 500 | 6.85% | $6,850 | 80 | 0 |
| 0941.HK | $86 | 500 | 6.85% | $6,850 | 79 | 0 |
| 0700.HK | $460 | 100 | 6.85% | $6,850 | 14 | 0 |

→ 一手都買唔起,17 條 `SKIP ... (lot rounding → qty=0)` 喺今日 HK 時段。

### 4.3 點解美股冇事

美股一股一手,$6850 / $200 = 34 股 — 直接落單,冇 rounding 問題。

---

## 5. 數據佐證（今日 2026-05-20 HK 時段）

| 指標 | 計數 |
|---|---:|
| Decisions written (port-overall) | 3,070 (但 16:00 後就停) |
| `model_buy=True` cycle | 756 |
| `swing_buy=True` cycle | 188 |
| `KELLY_ZERO_EDGE` skip | **844** |
| `SKIP ... lot rounding → qty=0` | 17 |
| `PRE_CHECK Broker qty=0 — skipping` (SELL) | 164 |
| 實際 `EXEC BUY` log | **0** |
| `BuyingPowerGuard` reject | 0 |

`v3_live.log` 顯示 14:00–16:00 期間 0 個 BUY,所有 cycle 都 fall through 三層 gate。

---

## 6. 連帶 design issues

### 6.1 SHORT vs LONG `zero_edge` 處理不一致
```
SHORT path line 1205: risk_pct = kelly['capped_fraction'] if not kelly['zero_edge'] else confidence_to_risk_pct(...)
LONG  path line 1343: if kelly['zero_edge']: return        # skip entirely
SWING path line 1794: if kelly_swing['zero_edge']: swing_risk_pct = confidence_to_risk_pct(...)
```
→ SHORT 同 swing 跌返 confidence sizing 繼續,model BUY 完全 abort。即係今日 HK 時段:
- model_buy + Kelly skip ⇒ 844 abort
- swing_buy + Kelly skip ⇒ 用 confidence_to_risk_pct ⇒ alloc 仲係太細 ⇒ lot rounding qty=0
- 兩條 path 都死,但死法唔同。

### 6.2 `risk_multiplier` 對 Kelly 唔起作用
MarketContext `risk_on x1.3` 經 `apply_macro_filter` 影響 `risk_pct`(line 1434),但 `zero_edge` 判斷喺 Kelly 內部完成,risk_multiplier 完全唔 reach Kelly→唔會 unblock。

### 6.3 DB schema 分裂
`self_learn.db` schema 同 `trading_bot.db` schema 各自獨立 init,新加 column(例如 `prediction_error`)只 migrate 其中一個。`schema.py` 同 `models.py` 應該 unify。

---

## 7. 建議修正路徑

### Phase 1 — 即時止血 (1–2 日,先令港股有 trade)
1. **修 `_round_to_lot` 之前 floor**：swing/model BUY path 喺 alloc 計完之後加 `alloc = max(alloc, lot * price)`,即至少夠買一手；或 alloc < 一手就跳低 cap 而非直接 0。配合 `max_position_value` 提至少 $10k USD。
2. **Disable `KELLY_ZERO_EDGE` skip for HK**:LONG path line 1343 加 `if symbol.endswith('.HK'): risk_pct = confidence_to_risk_pct(confidence)` 並 continue,**保留 ROR_GATE 仍然 enforce**。即時 unblock 港股 entry,等收集真實 outcome。
3. **Sanity 監控**：每個 HK BUY skip 加 structured event,Telegram 每日 summarize SKIP 原因(KELLY/LOT/ROR/CAP)。

### Phase 2 — 修 self-learn closure (1 週)
1. **加 `hook_on_signal` 喺 swing path (1801) 同 model path (1858)**：複製 line 1530-1543 嘅 try/except 結構。
2. **修 `_pred_id_by_symbol[symbol]`**：confirm `save_prediction` 嘅 ID 真係寫入 dict(`hook_on_signal` 嗰陣讀返,line 1534)。
3. **DB unify**:刪 `self_learn/self_learn.db`,只保留 `trading_bot.db`;`schema.py` 移除或重定向。
4. **Backfill historical outcomes**:從 `trades.jsonl` 重建 prediction→signal→outcome 鏈,起碼填 N=100。
5. **Test gate**: `tests/test_meta_labeler_integration.py` 已存在,加 `test_swing_signal_logs_signal`、`test_model_signal_logs_signal` 兩條。

### Phase 3 — 重設 Kelly 數據基礎 (2-4 週)
1. **替 `stress_test` input** 由 raw daily returns 改做 **conditional outcomes**：
   - Online: 由 self-learn outcomes 表取近 N=50 條同 symbol(或 same regime)嘅 pnl_pct
   - Bootstrap: cold-start 階段用 backtest 出嚟嘅 signal-conditional returns,而非全市場 raw returns
2. **`MonteCarloConfig.horizon` 改用 hold horizon**:由 `swing_buy` 嘅 expected hold 估計(可由 `max_hold_bars` × bar minutes / 60 / 24 推算),預設 5 日唔好用 20。
3. **加 minimum sample guard**:`if len(outcomes) < 30: kelly = None; risk_pct = confidence_to_risk_pct(...)` — 數據未足夠唔用 Kelly。
4. **Unify SHORT/LONG zero_edge 處理**:全部 fallback confidence sizing,唔再 silent abort。
5. **Reverse-prove with backtests**:`matrix_backtest/` 跑 HK universe,confirm 新 Kelly 邏輯有 measurable Sharpe 提升先 ship。

### Phase 4 — Self-learn 全鏈設計化 (1 個月+)
1. **MetaLabeler 整合**:`self_learn/meta_labeler.py`(已存在 untracked)做 second-pass filter,Kelly + Meta confidence 雙 gate。
2. **Online retrain trigger** 由 `RETRAIN_MIN_OUTCOMES` 改做 hybrid (outcomes 數 + days since last train + drift detection)。
3. **DB 由 SQLite 換 DuckDB**(或 Postgres):兩個 DB 一齊用 SQLite 已經有 WAL 文件殘留(`kiro_quant.db-wal`)。

---

## 8. 風險矩陣

| 改動 | Reversible | 主要風險 |
|---|---|---|
| Phase 1 bypass Kelly for HK | ✅ feature flag | 港股可能 cold-start 時 over-trade,但 stop_loss=2% / max_positions=15 / max_position_fraction=30% 應有保護 |
| Phase 1 alloc floor to 1 lot | ✅ flag | 細股單一手已可能超 confidence 對應嘅 risk_pct,需要 ROR_GATE 補位 |
| Phase 2 hook_on_signal 補位 | ✅ pure addition | 多寫 DB 可能輕微 perf 影響,但 32k preds 已經寫住,signals 1 個/cycle 唔重 |
| Phase 3 改 stress_test input | ❌ 大改 | 改動會 invalidate 現有 Kelly 行為觀察,需要 backtest 雙跑驗證 |
| Phase 3 horizon 改細 | ⚠️ 中 | win_rate 對 horizon 敏感,要重新 calibrate `kelly_factor=0.5` |

---

## 9. Open questions

1. 港股 `xgb_confidence=0.1` 嘅 threshold 係 model 訓練時設定 ge,而 model 大部分 cycle pred < current(全 sell)→ HK model retrain 時用嘅 target/feature 應該 audit(separate scope)。
2. `MonteCarloSimulator` 同樣用 `returns` 出 var95/cvar95 影響 `RiskController.allow_trade_with_ror`(line 1210-1219),即 ROR_GATE 嘅 fail rate 都可能由同樣嘅 raw-returns mismatch 觸發,值得另開一條 thread audit。
3. `latest_outcome.json` 有 record 但 DB 冇 — 或者 `_record_outcome` 拋 exception(被 `except Exception: pass` 食咗),或者 commit 失敗。可能要加 logger 確認。

---

## 10. 文件 cross-reference

- `v3_pipeline/risk/kelly_sizer.py` — Kelly 公式,本身正確
- `v3_pipeline/core/monte_carlo.py` — stress_test 用 raw returns,bug source
- `v3_pipeline/core/main_loop.py:1199,1335-1350,1786-1803,1808-1862` — 4 個 Kelly 用 path
- `v3_pipeline/core/main_loop.py:2354-2382` — on_trade_closed call(SELL 後)
- `self_learn/models.py` — log_signal / record_outcome 實裝,SQLAlchemy 寫入 `trading_bot.db`
- `self_learn/schema.py` — 同名 schema 但寫 `self_learn.db`(orphan)
- `self_learn/feedback.py` — `on_trade_closed` 公開 API
- `dev/meta_labeling/DEVLOG.md:194` — 已記錄 hook_on_signal 接駁問題
- `tests/test_meta_labeler_integration.py` — 已寫 outcome 寫入 test
- `tests/test_kelly_position_sizing.py` — Kelly 單元測試(未涵蓋 raw returns mismatch)
