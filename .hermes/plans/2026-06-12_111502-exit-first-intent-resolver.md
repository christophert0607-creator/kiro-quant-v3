# Exit-first Intent Resolver 重構計劃

## Goal

將 Kiro Quant V3 目前混亂嘅 trading decision flow 重構成可推理、可測試、可追蹤嘅 **exit-first intent resolver**。

核心目標：

1. **持倉安全出口優先**：只要 broker 有倉，exit path 必須先於任何 entry / short entry logic。
2. **消除 zombie state**：避免 broker 有貨但 internal/pre-check 判 0，導致 SELL 被 skip。
3. **分離 entry gate 與 exit safety check**：trade quality / meta / ROR 只應限制入場，不應阻止平倉。
4. **統一 trace**：每日報告可以清楚分辨 `SIGNAL → INTENT → GATE → EXEC_ATTEMPT → BROKER_RESULT`。
5. **降低 patch 疊 patch 風險**：將目前大 function 逐步拆成小型 resolver，不做一次性大爆炸 rewrite。

## Current context / assumptions

目前 repo：

- Source root：`/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3`
- 主要檔案：`v3_pipeline/core/main_loop.py`
- 現有 bridge flow：`check_and_trade()` → `_run_trading_logic_bridge()`
- 舊 legacy flow：`_run_trading_logic()` 仍然存在，但實際 live bridge 主要走 `_run_trading_logic_bridge()`。

已觀察到嘅問題：

- 美股 broker sync 有倉，例如 `QCOM/CRM/IBM/ORCL/AMZN`。
- `EXIT_QTY_RESOLVE` 正確顯示 `effective_qty > 0`。
- 但 `_execute(... SELL ...)` 內 live pre-check 可能用 snapshot exact code match 得到 `Broker qty=0 < requested=...`，令 SELL skip。
- `model_sell=True` / `qty > 0` / `SELL_PLACED=0` / `ORDER=0` 之間 trace 不完整，難以定位最終 block layer。
- `main_loop.py` 同時存在長倉、短倉、swing、model、stop-loss、time-exit、broker-sync repair、execution state machine 等多層邏輯，return/elif 順序容易斬斷正確 flow。

重要原則：

- **broker position 是 exit source of truth**。
- `position_qty_by_symbol` 只可視為 cache。
- `state.json` 只可作 fallback，不可凌駕 broker sync。
- EXIT_LONG / EXIT_SHORT 是 safety action，不應被 entry quality gate 擋。

## Proposed architecture

建立一條清楚 pipeline：

```text
State Sync
  → Signal Evaluation
  → Intent Resolver
  → Gate Evaluation
  → Execution Engine
  → Result Trace
```

### New core object: TradeIntent

建議新增 dataclass：

```python
@dataclass(frozen=True)
class TradeIntent:
    symbol: str
    action: Literal[
        "EXIT_LONG",
        "ENTER_LONG",
        "EXIT_SHORT",
        "ENTER_SHORT",
        "HOLD",
    ]
    side: Literal["BUY", "SELL", "NONE"]
    qty: int
    reason: str
    source: str
    priority: int
    price: float
    prediction: float | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

Priority 建議：

- `100` emergency/risk exit，例如 stop-loss、single-order loss hard exit
- `95` profit-protection activation：賣出前必須先計算止盈啟動價；若現價觸發，優先進入保護利潤模式（partial take-profit + trailing stop），而唔係無腦全賣
- `90` broker retry exit
- `80` model/swing long exit
- `70` short cover / short stop
- `50` entry
- `0` hold

## Step-by-step plan

### Phase 0 — Stabilize current patch surface

目的：避免重構時同 runtime artifacts / unrelated dev files 混埋。

Actions：

1. 檢查 git 狀態，只針對 trading logic 相關 files 開 branch。
2. 將 runtime artifacts 排除出 PR：
   - `kiro_quant.db*`
   - `state.json` unless intentionally changed
   - `v3_pid.txt`
   - `logs/*`
   - `__pycache__/*`
3. 保留或整理已存在嘅 targeted fix：SELL pre-check 使用 synced broker qty fallback。

Expected output：

- clean branch，例如：`fix/exit-first-intent-resolver-phase1`
- diff 只包含 source/tests/docs。

### Phase 1 — Add regression tests before refactor

新增測試集中喺 exit safety，不改 production behavior 先。

Likely file：

- `tests/test_exit_path_broker_position_sync.py`
- 可新增：`tests/test_trade_intent_resolver.py`

Required tests：

1. `test_long_exit_intent_uses_broker_qty_when_internal_qty_zero`
   - internal qty = 0
   - broker synced qty > 0
   - model sell signal true
   - expected：intent/action = `EXIT_LONG`, qty = broker qty

2. `test_take_profit_exit_has_priority_over_model_and_swing_signals`
   - long qty > 0
   - entry price known
   - current price >= calculated take-profit price
   - model/swing may be neutral or noisy
   - expected：intent/action = `EXIT_LONG`, reason starts with `take_profit`, priority 95

3. `test_long_exit_has_priority_over_short_entry_signal`
   - long qty > 0
   - prediction below threshold
   - short_enabled = true
   - expected：`EXIT_LONG`，不可 `ENTER_SHORT`

4. `test_exit_not_blocked_by_trade_quality_or_meta_label_gate`
   - trade quality reject / meta reject mocked
   - qty > 0 + exit signal
   - expected：SELL intent / execution attempt still emitted

5. `test_live_sell_precheck_allows_synced_broker_qty_when_snapshot_misses_symbol`
   - pre-check snapshot miss symbol
   - `broker_position_qty_by_symbol[symbol] >= qty`
   - expected：broker `place_order` called

6. `test_every_hold_or_skip_records_decision_reason`
   - no order case should write intent/gate trace reason, not silent return

RED verification：

```bash
PYTHONPATH=. pytest tests/test_exit_path_broker_position_sync.py -q
PYTHONPATH=. pytest tests/test_trade_intent_resolver.py -q
```

### Phase 2 — Introduce intent module without rewiring all live flow

Create new module:

- `v3_pipeline/core/trade_intents.py`

Contents：

- `TradeIntent`
- `TradeAction` enum or Literal aliases
- helper constructors:
  - `hold_intent(...)`
  - `exit_long_intent(...)`
  - `enter_long_intent(...)`
  - `exit_short_intent(...)`
  - `enter_short_intent(...)`

No side effects in this module.

Validation：

```bash
python -m py_compile v3_pipeline/core/trade_intents.py
```

### Phase 3 — Extract long exit resolver first

Create method or module function：

```python
def resolve_long_exit_intent(
    *,
    symbol: str,
    current_price: float,
    prediction: float,
    confidence: float,
    qty: int,
    entry_price: float,
    bars_held: int,
    threshold_down: float,
    swing_sell_signal: bool,
    retry_reason: str | None,
    config: LiveConfig,
) -> TradeIntent:
    ...
```

Rules：

1. If `retry_reason and qty > 0` → `EXIT_LONG`, reason `retry_<reason>`, priority 90。
2. Stop loss / emergency exit first。
3. **Profit-protection check must run before model/swing sell**：
   - compute `profit_activation_price = entry_price * (1 + take_profit_activation_pct)`
   - if `current_price >= profit_activation_price`，唔即刻全賣；先進入 profit-protection mode
   - default action：partial sell 30% of qty，reason `partial_take_profit_<pct>`，priority 95
   - remaining qty enters trailing stop mode
   - if trend is strong，skip/減少 partial sell，use wider trailing stop to let winner run
   - this prevents a profitable holding from being delayed by model confirmation / swing noise, while avoiding premature full exit during strong uptrends
4. Quick take profit / max hold / swing sell。
5. Model sell only if `prediction < threshold_down` and cooldown satisfied。
6. Else `HOLD` with reason，例如：
   - `no_long_position`
   - `cooldown_active`
   - `no_exit_signal`

Important：

- This resolver should not call `_execute()`。
- This resolver should not query broker directly。
- It receives already-resolved `qty` from `_effective_long_qty_for_exit()`。

Likely changes：

- `v3_pipeline/core/main_loop.py`
- `v3_pipeline/core/trade_intents.py`
- `tests/test_trade_intent_resolver.py`

### Phase 3.5 — Profit protection / let-winners-run policy

Add config knobs：

```python
profit_protection_enabled: bool = True
take_profit_activation_pct: float = 0.03
partial_take_profit_enabled: bool = True
partial_take_profit_fraction: float = 0.30
trailing_after_profit_pct: float = 0.012
strong_trend_trailing_pct: float = 0.020
weak_trend_trailing_pct: float = 0.008
strong_trend_partial_fraction: float = 0.0  # strong trend 可以唔先賣，讓利潤奔跑
```

Strong trend definition，第一版可用簡單規則：

```text
price > SMA20
SMA5 > SMA20
MACD_HIST > 0
prediction >= current_price
RSI < 75
```

Behavior：

1. 未到 `profit_activation_price`：正常持倉。
2. 到達 `profit_activation_price`：
   - if strong trend：啟動較寬 trailing stop，預設不賣或只賣很小部分。
   - if not strong trend：先 partial sell 30%，剩餘持倉啟動 trailing stop。
3. trailing stop 觸發時才賣剩餘倉位。
4. stop-loss 永遠可以覆蓋 profit-protection，避免贏變大輸。

Trace requirements：

- `PROFIT_PROTECTION_ACTIVATED`
- `PARTIAL_TAKE_PROFIT_PLACED`
- `TRAILING_PROFIT_HOLD`
- `TRAILING_PROFIT_EXIT`

### Phase 4 — Wire resolver into `_run_trading_logic_bridge()`

In `_run_trading_logic_bridge()`:

1. Resolve effective qty early:

```python
qty = self._effective_long_qty_for_exit(symbol)
```

2. If `qty > 0`, call long exit resolver before any entry logic:

```python
intent = resolve_long_exit_intent(...)
self._trace_intent(intent)
if intent.action == "EXIT_LONG":
    self._execute(symbol, "SELL", intent.qty, current_price, intent.reason)
    return
```

3. Only if no long position / no exit intent, continue entry logic。

Critical invariant：

```python
if qty > 0:
    # No ENTER_LONG / ENTER_SHORT before long exit resolver finishes.
```

### Phase 5 — Separate entry gates from exit safety checks

Add explicit naming:

- `_entry_gates_allow(...)` — only BUY / SHORT entry
- `_exit_safety_precheck(...)` — only validates SELL/COVER qty/source; no quality/meta/ROR

Expected behavior：

- `TRADE_QUALITY_GATE` cannot block `EXIT_LONG`。
- `META_LABEL_GATE` cannot block `EXIT_LONG`。
- order rate limit cannot block `SELL`。
- broker pre-check may block only when all trusted sources show insufficient qty。

### Phase 6 — Improve trace schema

Add structured events:

1. `signal_snapshot`
   - model_buy_signal
   - model_sell_signal
   - swing_buy
   - swing_sell
   - qty
   - broker_qty

2. `trade_intent`
   - action
   - qty
   - reason
   - source
   - priority

3. `gate_decision`
   - gate
   - decision: ALLOW / BLOCK
   - reason

4. `execution_attempt`
   - symbol
   - side
   - qty
   - price
   - reason

5. `broker_result`
   - accepted/rejected
   - broker_order_id if available
   - error if rejected

This will make daily report able to say:

```text
model_sell=True: 517
EXIT_LONG intent: 143
execution_attempt SELL: 143
broker_rejected/precheck_blocked: 12
filled/accepted: N
```

instead of only `SELL_PLACED=0`。

### Phase 7 — Short path extraction, after long exit stable

Only after long exit tests green，抽：

- `resolve_short_exit_intent(...)`
- `resolve_short_entry_intent(...)`

Rules：

- Short entry only when `long_qty == 0 and short_qty == 0`。
- If `long_qty > 0 and prediction < threshold_down`，first intent is `EXIT_LONG`，not `ENTER_SHORT`。
- Flip long→short requires two cycles or explicit transition state，不可同 cycle sell+short unless deliberately enabled。

### Phase 8 — Remove/deprecate legacy `_run_trading_logic()`

After bridge path is covered：

1. Search references to `_run_trading_logic()`。
2. If unused，mark deprecated or remove in separate PR。
3. If still used by old scripts/tests，redirect through resolver or add warning。

Do not mix this with Phase 1 PR unless diff remains small。

## Files likely to change

Primary：

- `v3_pipeline/core/main_loop.py`
- `v3_pipeline/core/trade_intents.py` （new）
- `tests/test_exit_path_broker_position_sync.py`
- `tests/test_trade_intent_resolver.py` （new）

Possible later：

- `v3_pipeline/execution/state_machine.py`
- `v3_pipeline/core/decision_events.py`
- `tests/test_main_loop_trade_bridge.py`
- `docs/` or `references/` for flow diagram / runbook

Avoid unless necessary：

- `config.json`
- `state.json`
- DB/log/runtime files
- model binaries

## Tests / validation

Focused tests：

```bash
PYTHONPATH=. pytest tests/test_exit_path_broker_position_sync.py -q
PYTHONPATH=. pytest tests/test_trade_intent_resolver.py -q
PYTHONPATH=. pytest tests/test_main_loop_trade_bridge.py -q
```

Compile：

```bash
python -m py_compile \
  v3_pipeline/core/main_loop.py \
  v3_pipeline/core/trade_intents.py \
  tests/test_exit_path_broker_position_sync.py \
  tests/test_trade_intent_resolver.py
```

Static sanity：

```bash
git diff --stat
git diff -- v3_pipeline/core/main_loop.py v3_pipeline/core/trade_intents.py tests/test_exit_path_broker_position_sync.py tests/test_trade_intent_resolver.py
```

Runtime smoke，read-only / safe mode first：

```bash
NO_FUTU=1 AUTO_TRADE=0 PYTHONPATH=. python v3_launcher.py --once
```

Live safety smoke only after user approval：

- Confirm FutuOpenD connected。
- Confirm sandbox/live account target。
- Confirm no unintended BUY path enabled during test。
- Observe one cycle logs for held US symbols。

## Risks / tradeoffs

### Risk 1 — Exit resolver changes live behavior

Mitigation：

- Start with long exit only。
- Tests must cover current expected behavior。
- Entry logic untouched in Phase 1 except early return after exit intent。

### Risk 2 — Broker qty fallback may allow stale sell

Mitigation：

- Use synced broker qty only if last sync succeeded in current/near-current cycle if available。
- Trace when fallback is used。
- Future improvement：store `broker_sync_ts_by_symbol` and require freshness threshold。

### Risk 3 — Tests currently depend on environment packages

Known issue：

- Some existing tests fail due to missing `sqlalchemy` or monkeypatched `self_learn` package behavior。

Mitigation：

- Keep new tests isolated with `LiveTradingLoop.__new__` and simple stubs。
- Avoid importing optional runtime deps in resolver tests。
- If needed, patch test fixtures separately in a dedicated cleanup PR。

### Risk 4 — Too large PR

Mitigation：

Split PRs：

1. `fix: make live SELL precheck trust synced broker qty`
2. `refactor: add TradeIntent and long exit resolver`
3. `refactor: wire exit-first bridge path`
4. `refactor: isolate entry/short resolvers`
5. `docs/tests: improve trace and report counters`

## Open questions

1. Should model sell exit require confirmation streak for all US names, or should broker-held zombie positions bypass streak?
2. Should long→short flip be allowed same cycle, or require exit accepted first then next cycle short entry?
3. How fresh must `broker_position_qty_by_symbol` be before it can override pre-check snapshot?
4. Should max-hold / stop-loss / single-order-loss exits always bypass cooldown?
5. Should daily report count `SELL_INTENT`, `SELL_ATTEMPT`, `SELL_ACCEPTED`, `SELL_FILLED` separately?

## Recommended first implementation slice

Do **not** rewrite everything at once。

First PR should only do：

1. Add `TradeIntent` dataclass。
2. Extract `resolve_long_exit_intent()`。
3. Wire only `qty > 0` long exit branch before entry logic。
4. Keep existing entry path unchanged。
5. Add tests proving:
   - broker qty source of truth
   - exit priority over short/entry
   - entry gates do not block exit
   - trace exists for hold/skip

成功標準：

```text
有 broker 長倉 + exit signal → 必定產生 EXIT_LONG intent → 必定到 execution attempt，除非 exit safety pre-check 有明確、可 trace、可信原因 block。
```
