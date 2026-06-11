# V3 Exit Path Broker Position Repair Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Fix V3 so broker-held positions always trigger exit logic, even after restart or internal `position_qty_by_symbol` desync.

**Architecture:** Treat broker positions as the highest truth source for exits. Before stop-loss / take-profit / model-sell / swing-sell decisions, resolve an effective long quantity from broker sync + internal state, hydrate missing entry metadata conservatively, and add retry handling for cancelled SELL orders while keeping BUY gates unchanged.

**Tech Stack:** Python 3, pytest, FutuOpenD SIMULATE, `v3_pipeline/core/main_loop.py`, `v3_pipeline/core/futu_connector.py`, `logs/decisions.jsonl`.

---

## Evidence / Problem Statement

Observed on `2026-06-05`:

- Broker position sync showed holdings, e.g. `Position sync [BROKER][LONG]: QCOM = 806`.
- Trading decision logs showed actual held symbols as `qty=0`, e.g. `DIAG_GATE[INTC] ... qty=0 ... model_sell=True`.
- Exit code requires `qty > 0` before SELL branches fire:
  - `_run_trading_logic_bridge()` lines around `2022-2055` for stop / TP / max-hold.
  - `_run_trading_logic_bridge()` lines around `2140-2142` for swing sell.
  - `_run_trading_logic_bridge()` lines around `2211-2213` for model sell.
- QCOM had a `SELL 806` order that became `CANCELLED_ALL`, but no durable retry path forced liquidation while position remained.

Root cause: internal strategy quantity can become stale or zero even when broker holds a position. Exit logic trusts internal quantity too much.

Non-goals:
- Do not loosen BUY gates.
- Do not make MetaLabel enforcement live.
- Do not change account risk sizing except to protect existing exit paths.
- Do not auto-flatten all positions by default; this is a mechanical exit-path repair.

---

## Acceptance Criteria

1. If broker reports a long position for `symbol`, effective exit qty is non-zero even when `position_qty_by_symbol[symbol] == 0`.
2. Stop-loss / take-profit / max-hold / swing-sell / model-sell use effective broker-backed qty.
3. SELL / risk exits are never blocked by TradeQuality, MetaLabel, `max_orders_per_cycle`, or BUY-only throttle.
4. If a SELL order is cancelled/rejected and broker position remains, next cycle records a retry-needed marker and attempts SELL again when exit condition is still true.
5. Logs clearly show broker/internal mismatch and exit decisions:
   - `[POSITION_SYNC_REPAIR]`
   - `[EXIT_QTY_RESOLVE]`
   - `[SELL_RETRY_REQUIRED]`
   - `[EXIT_ORDER_ATTEMPT]`
6. Regression tests reproduce the original bug: internal qty `0`, broker qty `>0`, `model_sell=True` must call `_execute(..., "SELL", broker_qty, ...)`.
7. Verification bundle passes:
   - `python3 -m py_compile v3_pipeline/core/main_loop.py v3_pipeline/core/futu_connector.py v3_launcher.py`
   - `PYTHONPATH=. pytest tests/test_exit_path_broker_position_sync.py tests/test_live_execution_ordering.py tests/test_futu_connector_unlock.py -q`

---

## Task 1: Add failing regression test for model-sell with broker qty

**Objective:** Prove the current bug: broker has qty but internal qty is zero, so model sell should still execute.

**Files:**
- Create: `tests/test_exit_path_broker_position_sync.py`
- Modify: none

**Step 1: Create test file**

Add:

```python
from types import SimpleNamespace

from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop


class DummyRiskController:
    def circuit_breaker_triggered(self, *_args, **_kwargs):
        return False

    def allow_trade_with_ror(self, *_args, **_kwargs):
        return True

    def allow_daily_loss(self, *_args, **_kwargs):
        return True


class DummySwing:
    def stress_test(self, *_args, **_kwargs):
        return {"win_rate": 0.5, "avg_win": 0.01, "avg_loss": 0.01, "var95": 0.0}


def make_loop():
    cfg = LiveConfig(
        symbols_list=["QCOM"],
        auto_trade=True,
        paper_trading=True,
        prediction_thresholds={"QCOM": 0.01},
        swing_strategy_enabled=False,
        diagnostics_verbose=True,
        log_trade_decisions=True,
        buy_cooldown_cycles=0,
    )
    loop = LiveTradingLoop.__new__(LiveTradingLoop)
    loop.config = cfg
    loop.account_value = 1_000_000.0
    loop.equity_peak = 1_000_000.0
    loop.risk_controller = DummyRiskController()
    loop.position_qty_by_symbol = {"QCOM": 0}
    loop.broker_position_qty_by_symbol = {"QCOM": 806}
    loop.entry_price_by_symbol = {}
    loop.entry_rsi_by_symbol = {}
    loop.bars_held_by_symbol = {"QCOM": 0}
    loop.cycles_since_buy_by_symbol = {"QCOM": 999}
    loop.highest_price_since_entry_by_symbol = {}
    loop.short_position_qty_by_symbol = {"QCOM": 0}
    loop.bars_held_short_by_symbol = {"QCOM": 0}
    loop.cycles_since_short_by_symbol = {"QCOM": 999}
    loop.buy_cover_signal_streak_by_symbol = {"QCOM": 0}
    loop.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    loop._notify = lambda *_args, **_kwargs: None
    loop._get_buffer = lambda _symbol: __import__("pandas").DataFrame({"Close": [230.0, 229.0, 228.0]})
    loop._evaluate_swing_signal = lambda *_args, **_kwargs: {"buy_signal": False, "sell_signal": False}
    loop._entry_gates_allow = lambda *_args, **_kwargs: True
    loop._refresh_daily_loss_anchor = lambda: None
    loop.day_start_equity = 1_000_000.0
    calls = []
    loop._execute = lambda *args, **kwargs: calls.append((args, kwargs))
    loop._test_execute_calls = calls
    return loop


def test_model_sell_uses_broker_qty_when_internal_qty_zero():
    loop = make_loop()

    loop._run_trading_logic_bridge(
        symbol="QCOM",
        current_price=235.49,
        prediction=220.00,  # > 1% below current => model_sell=True
        confidence=1.0,
        allow_long=True,
        latest_frame=None,
        pattern_label="DownTrend",
        pattern_confidence=1.0,
    )

    assert loop._test_execute_calls, "expected SELL to be executed using broker qty"
    args, _kwargs = loop._test_execute_calls[0]
    assert args[0] == "QCOM"
    assert args[1] == "SELL"
    assert args[2] == 806
    assert args[4] == "model_signal"
```

**Step 2: Run failing test**

Run:

```bash
PYTHONPATH=. pytest tests/test_exit_path_broker_position_sync.py::test_model_sell_uses_broker_qty_when_internal_qty_zero -q
```

Expected before implementation: FAIL because no SELL call occurs.

---

## Task 2: Add broker-backed effective qty resolver

**Objective:** Centralize long-position quantity resolution and log internal/broker mismatch.

**Files:**
- Modify: `v3_pipeline/core/main_loop.py`
- Test: `tests/test_exit_path_broker_position_sync.py`

**Step 1: Add helper methods to `LiveTradingLoop` before `_run_trading_logic_bridge()`**

Add near line before `def _run_trading_logic_bridge`:

```python
    def _broker_long_qty_for_symbol(self, symbol: str) -> int:
        """Return broker-synced long qty for symbol if available.

        Broker positions are the source of truth for exits.  This method is
        intentionally tolerant of missing attributes because tests and older
        runtime paths may not initialise every sync map.
        """
        maps = [
            getattr(self, "broker_position_qty_by_symbol", None),
            getattr(self, "position_qty_by_symbol_broker", None),
        ]
        for mapping in maps:
            if isinstance(mapping, dict):
                try:
                    qty = int(float(mapping.get(symbol, 0) or 0))
                except Exception:
                    qty = 0
                if qty > 0:
                    return qty
        return 0

    def _effective_long_qty_for_exit(self, symbol: str) -> int:
        """Resolve long qty for exit decisions using broker truth first."""
        try:
            internal_qty = int(float(self.position_qty_by_symbol.get(symbol, 0) or 0))
        except Exception:
            internal_qty = 0
        internal_qty = max(0, internal_qty)
        broker_qty = self._broker_long_qty_for_symbol(symbol)
        effective_qty = max(internal_qty, broker_qty)
        if broker_qty > internal_qty:
            self.logger.warning(
                "[POSITION_SYNC_REPAIR][%s] broker_qty=%d internal_qty=%d -> effective_qty=%d",
                symbol,
                broker_qty,
                internal_qty,
                effective_qty,
            )
            self.position_qty_by_symbol[symbol] = effective_qty
        self.logger.info(
            "[EXIT_QTY_RESOLVE][%s] internal_qty=%d broker_qty=%d effective_qty=%d",
            symbol,
            internal_qty,
            broker_qty,
            effective_qty,
        )
        return effective_qty
```

**Step 2: Run targeted test**

```bash
PYTHONPATH=. pytest tests/test_exit_path_broker_position_sync.py -q
```

Expected: still may fail until Task 3 wires helper into `_run_trading_logic_bridge()`.

---

## Task 3: Wire effective qty into bridge exit branches

**Objective:** Ensure all exit branches see broker-backed qty.

**Files:**
- Modify: `v3_pipeline/core/main_loop.py:2022-2055, 2140-2142, 2211-2213`
- Test: `tests/test_exit_path_broker_position_sync.py`

**Step 1: Replace qty resolution in `_run_trading_logic_bridge()`**

Current pattern:

```python
        qty_raw = int(self.position_qty_by_symbol.get(symbol, 0))
        qty = max(0, qty_raw)
```

Replace with:

```python
        qty_raw = int(self.position_qty_by_symbol.get(symbol, 0))
        qty = self._effective_long_qty_for_exit(symbol)
```

Keep the negative-qty warning but make it based on `qty_raw` only.

**Step 2: Ensure entry branches still use `qty == 0` after repair**

No code change if `qty` now means effective qty. This is desired: if broker has position, no new BUY should be opened for that symbol.

**Step 3: Run test**

```bash
PYTHONPATH=. pytest tests/test_exit_path_broker_position_sync.py::test_model_sell_uses_broker_qty_when_internal_qty_zero -q
```

Expected: PASS.

---

## Task 4: Add stop-loss regression using broker qty and hydrated entry price

**Objective:** Ensure stop-loss can fire for broker-held positions after restart.

**Files:**
- Modify: `tests/test_exit_path_broker_position_sync.py`
- Modify: `v3_pipeline/core/main_loop.py` if needed

**Step 1: Add failing test**

```python
def test_stop_loss_uses_broker_qty_when_internal_qty_zero():
    loop = make_loop()
    loop.entry_price_by_symbol["QCOM"] = 240.0
    loop.config.stop_loss_pct = 0.02

    loop._run_trading_logic_bridge(
        symbol="QCOM",
        current_price=235.0,  # below 240 * 0.98 = 235.2
        prediction=250.0,
        confidence=0.8,
        allow_long=True,
    )

    args, _kwargs = loop._test_execute_calls[0]
    assert args[1] == "SELL"
    assert args[2] == 806
    assert args[4].startswith("stop_loss_")
```

**Step 2: Run**

```bash
PYTHONPATH=. pytest tests/test_exit_path_broker_position_sync.py::test_stop_loss_uses_broker_qty_when_internal_qty_zero -q
```

Expected: PASS after Task 3.

---

## Task 5: Persist broker position map during sync

**Objective:** Make `_broker_long_qty_for_symbol()` backed by real sync data, not just tests.

**Files:**
- Modify: `v3_pipeline/core/main_loop.py`, broker sync section around lines `2335-2423`

**Step 1: Find broker sync code**

Look around existing lines:

```python
self.position_qty_by_symbol[matched_symbol] = max(0, qty)
```

and:

```python
active_positions = {s: q for s, q in self.position_qty_by_symbol.items() if q > 0}
```

**Step 2: Initialize broker map in `__init__`**

Near existing `self.position_qty_by_symbol` initialization, add:

```python
        self.broker_position_qty_by_symbol: dict[str, int] = {s: 0 for s in _all_tracking}
```

**Step 3: Update broker map during position sync**

When a broker long qty is found:

```python
                        normalized_qty = max(0, int(qty))
                        self.broker_position_qty_by_symbol[matched_symbol] = normalized_qty
                        self.position_qty_by_symbol[matched_symbol] = normalized_qty
```

Before processing current broker snapshot, reset tracked broker quantities to zero to prevent stale holdings:

```python
                for _sym in list(getattr(self, "broker_position_qty_by_symbol", {}).keys()):
                    self.broker_position_qty_by_symbol[_sym] = 0
```

Only do this at the start of a successful broker position sync, not on failed sync.

**Step 4: Add log**

After sync:

```python
                broker_active = {
                    s: q for s, q in self.broker_position_qty_by_symbol.items() if q > 0
                }
                self.logger.info("[BROKER_POSITION_SYNC] active_longs=%s", broker_active)
```

**Step 5: Test**

```bash
PYTHONPATH=. pytest tests/test_exit_path_broker_position_sync.py tests/test_live_execution_ordering.py -q
```

Expected: PASS.

---

## Task 6: Do not let SELL be throttled by entry rate limit

**Objective:** Verify and lock behavior that `max_orders_per_cycle` only blocks BUY / SHORT entry, not SELL exits.

**Files:**
- Modify: `tests/test_exit_path_broker_position_sync.py`
- Inspect: `v3_pipeline/core/main_loop.py:_order_rate_limit_guard`, `_execute`, `_execute_short_entry`

**Step 1: Add test**

```python
def test_sell_exit_not_blocked_by_order_rate_limit():
    loop = make_loop()
    loop.config.max_orders_per_cycle = 0
    loop.config.order_throttle_seconds = 9999
    loop.entry_price_by_symbol["QCOM"] = 240.0

    loop._run_trading_logic_bridge(
        symbol="QCOM",
        current_price=235.0,
        prediction=220.0,
        confidence=1.0,
        allow_long=True,
    )

    assert loop._test_execute_calls
    assert loop._test_execute_calls[0][0][1] == "SELL"
```

**Step 2: Run**

```bash
PYTHONPATH=. pytest tests/test_exit_path_broker_position_sync.py::test_sell_exit_not_blocked_by_order_rate_limit -q
```

Expected: PASS.

If it fails, change `_execute()` so `_order_rate_limit_guard()` is called only for `side == "BUY"`. Short entry throttle should remain in `_execute_short_entry()`, not in SELL exits.

---

## Task 7: Add cancelled SELL retry marker

**Objective:** If a SELL order is cancelled/rejected and broker position remains, record retry requirement for next cycle.

**Files:**
- Modify: `v3_pipeline/core/main_loop.py:_execute`
- Modify: `tests/test_exit_path_broker_position_sync.py`

**Step 1: Add in-memory retry map in `__init__`**

```python
        self.sell_retry_required_by_symbol: dict[str, str] = {}
```

**Step 2: In `_execute()`, when side is SELL and broker result fails/cancels**

Implementation depends on existing broker call result shape. Pattern:

```python
            if side == "SELL" and not accepted:
                remaining_qty = self._broker_long_qty_for_symbol(symbol)
                if remaining_qty > 0:
                    self.sell_retry_required_by_symbol[symbol] = reason
                    self.logger.warning(
                        "[SELL_RETRY_REQUIRED][%s] reason=%s remaining_broker_qty=%d broker_status=%s",
                        symbol,
                        reason,
                        remaining_qty,
                        broker_status,
                    )
```

If `_execute()` currently only receives bool from broker, record retry on any false return for SELL.

**Step 3: At the top of `_run_trading_logic_bridge()`, before normal entry logic**

After effective qty resolution:

```python
        retry_reason = getattr(self, "sell_retry_required_by_symbol", {}).get(symbol)
        if retry_reason and qty > 0:
            self.logger.warning(
                "[SELL_RETRY_REQUIRED][%s] retrying previous failed SELL: reason=%s qty=%d",
                symbol,
                retry_reason,
                qty,
            )
            self._execute(symbol, "SELL", qty, current_price, f"retry_{retry_reason}")
            return
```

Clear retry marker only when broker position becomes zero or SELL accepted.

**Step 4: Add test using monkeypatched `_execute` if needed**

Test minimum:

```python
def test_sell_retry_marker_forces_next_cycle_sell():
    loop = make_loop()
    loop.sell_retry_required_by_symbol = {"QCOM": "model_signal"}

    loop._run_trading_logic_bridge(
        symbol="QCOM",
        current_price=235.49,
        prediction=250.0,
        confidence=0.5,
        allow_long=True,
    )

    args, _kwargs = loop._test_execute_calls[0]
    assert args[1] == "SELL"
    assert args[2] == 806
    assert args[4] == "retry_model_signal"
```

---

## Task 8: Structured decision trace for exit attempts

**Objective:** Make future diagnosis unambiguous.

**Files:**
- Modify: `v3_pipeline/core/main_loop.py:_execute`

**Step 1: Before broker/PAPER dispatch for SELL, append decision trace**

Use existing `_append_decision_trace()` if present. Add:

```python
        if side == "SELL":
            try:
                self._append_decision_trace({
                    "event": "exit_order_attempt",
                    "symbol": symbol,
                    "side": side,
                    "qty": int(qty),
                    "price": float(price),
                    "reason": reason,
                    "internal_qty": int(self.position_qty_by_symbol.get(symbol, 0) or 0),
                    "broker_qty": int(self._broker_long_qty_for_symbol(symbol)),
                    "paper": bool(self.config.paper_trading),
                })
            except Exception:
                pass
```

**Step 2: Verify by running a unit test or smoke call**

Expected JSON line in `logs/decisions.jsonl` during runtime:

```json
{"event":"exit_order_attempt","symbol":"QCOM","side":"SELL","qty":806,...}
```

---

## Task 9: Add runtime smoke script for current broker positions

**Objective:** Provide a safe read-only smoke test that tells whether exit truth source is aligned.

**Files:**
- Create: `scripts/check_exit_position_alignment.py`

**Step 1: Write script**

Script should:
- connect to Futu with `OpenSecTradeContext(filter_trdmarket=TrdMarket.NONE)`
- query HK SIM `14239754` and US SIM `18526451`
- print non-zero broker positions
- tail latest `broker_sync` in `logs/decisions.jsonl`
- report mismatches between broker positions and latest engine broker sync

**Step 2: Run**

```bash
python3 scripts/check_exit_position_alignment.py
```

Expected output:

```text
status=ok
broker_positions=...
latest_engine_sync=...
mismatches=0
```

If mismatches > 0, do not claim exit repair is runtime-verified.

---

## Task 10: Restart and verify during live/idle safely

**Objective:** Deploy repaired code without double-engine risk.

**Files:**
- Runtime only

**Step 1: Compile and test**

```bash
python3 -m py_compile v3_pipeline/core/main_loop.py v3_pipeline/core/futu_connector.py v3_launcher.py
PYTHONPATH=. pytest tests/test_exit_path_broker_position_sync.py tests/test_live_execution_ordering.py tests/test_futu_connector_unlock.py -q
```

Expected:

```text
... passed
```

**Step 2: Confirm only one launcher or kill cleanly**

```bash
ps aux | grep v3_launcher | grep -v grep
python3 - <<'PY'
import os, signal, subprocess, time
out=subprocess.check_output(['ps','-eo','pid,args'], text=True)
for line in out.splitlines():
    if 'python3 v3_launcher.py' in line and 'python3 - <<' not in line:
        pid=int(line.strip().split(None,1)[0])
        print('killing', pid, line.strip())
        os.kill(pid, signal.SIGTERM)
time.sleep(2)
PY
```

**Step 3: Start V3**

```bash
cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3
python3 v3_launcher.py > logs/dashboard-v3-launcher.out.log 2>&1
```

Use Hermes `terminal(background=true, notify_on_complete=true)` for the actual start.

**Step 4: Verify**

```bash
ps aux | grep -E 'v3_launcher|FutuOpenD|FTWebSocket' | grep -v grep
tail -100 logs/v3_live.log | grep -E 'MARKET_INIT|BROKER_POSITION_SYNC|POSITION_SYNC_REPAIR|EXIT_QTY_RESOLVE|EXIT_ORDER_ATTEMPT|SELL_RETRY_REQUIRED|Traceback|CRASH|ERROR'
```

Expected:
- one `v3_launcher.py`
- FutuOpenD alive
- no `Traceback / CRASH / ERROR`
- during market session, broker-held positions show effective qty resolution

---

## Task 11: Post-fix report template

**Objective:** Report in a way that proves the bug is fixed.

Use this format:

```text
Status: fixed / partially fixed / blocked
Tests: py_compile + pytest result
Runtime: PID + log timestamp
Broker truth: HK/US current positions
Exit truth: [EXIT_QTY_RESOLVE] examples
Sell proof: [EXIT_ORDER_ATTEMPT] examples or explanation if market closed
Risk: any remaining issue
Next action: observe next live session / manually flatten / keep B2
```

Do not say “識走” unless at least one of these is true:
- runtime log has `EXIT_ORDER_ATTEMPT` for broker-held qty, or
- live market closed but unit tests prove broker qty triggers SELL, and report says runtime sell proof pending.

---

## Task 12: Optional hardening after first fix

**Objective:** Improve durability after the immediate bug is fixed.

Future improvements:

1. Store broker order result with `order_id`, `status`, `dealt_qty` in `decisions.jsonl`.
2. Add separate `exit_max_orders_per_cycle` if we need emergency exit throttling; default unlimited for SELL.
3. Add `stale_internal_qty_detector` heartbeat: alert if broker position exists but `DIAG_GATE qty=0` appears for same symbol.
4. Add dashboard card: `Exit sync health = OK / DESYNC`.
5. Add scheduled daily check after close: find any symbol with `model_sell=True` + broker position + zero SELL attempt.

---

## Final Verification Bundle

Run from repo root:

```bash
cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3
python3 -m py_compile v3_pipeline/core/main_loop.py v3_pipeline/core/futu_connector.py v3_launcher.py
PYTHONPATH=. pytest tests/test_exit_path_broker_position_sync.py tests/test_live_execution_ordering.py tests/test_futu_connector_unlock.py -q
python3 scripts/check_exit_position_alignment.py
```

Expected:

```text
py_compile: no output / exit 0
pytest: all passed
alignment: status=ok or mismatches explicitly listed
```
