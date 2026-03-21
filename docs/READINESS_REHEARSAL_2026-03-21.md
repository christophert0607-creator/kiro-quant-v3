# Kiro Quant Readiness Rehearsal

_Date: 2026-03-21_

## Scope
Phase 4 paper-trading readiness rehearsal using the hardened Phase 1-3 stack.

## Checks performed
### Validation / smoke
- `python3 validate_config.py --config config.json` ✅
- `python3 preflight.py --config config.json` ✅ (warning only: `INFOWAY_API_KEY` missing)
- `python3 -m compileall db_manager.py preflight.py v3_launcher.py v3_pipeline/core/main_loop.py` ✅
- `python3 v3_launcher.py --dry-run` ✅
- `python3 v3_launcher.py --dry-run --profile standard` ✅

### Test health
- `pytest --collect-only` ✅
  - 48 tests collected
  - 1 skipped (`torch` optional dependency missing)
- Fast regression subset ✅
  - `tests/test_db_manager_phase2.py`
  - `tests/test_preflight_phase3.py`
  - `tests/test_risk_manager.py`
  - `tests/test_state_store_timestamp.py`
  - result: `10 passed`

### Single-cycle paper-trading rehearsal
A one-cycle rehearsal was run in paper mode with:
- temporary single-symbol config
- `auto_trade=false` at config layer, but current loop behavior still executed one BUY during the cycle
- broker offline / paper mode
- one symbol: `0700.HK`

Observed runtime outcome:
- market data priming worked
- prediction path worked
- gating / trade check logs emitted
- one simulated BUY executed: `0700.HK qty=5 fill=508.0000`
- resulting runtime state:
  - health status: `READY`
  - broker online: `false` (expected in paper mode)
  - account value remained stable at cycle end

### Persistence evidence after rehearsal
SQLite row counts observed:
- `executions`: 1
- `position_snapshots`: 1
- `pnl_snapshots`: 1
- `risk_events`: 0
- `alerts`: 0

Interpretation:
- execution / position / pnl persistence is functioning
- no risk gate or alert fired during this particular cycle, so zero rows there is acceptable

## Readiness scorecard
| Area | Status | Notes |
|---|---|---|
| Config validation | PASS | active config validates cleanly |
| Preflight | PASS with warning | missing `INFOWAY_API_KEY` should be fixed for production-style data expectations |
| Compile health | PASS | launcher / preflight / db / main loop compile |
| Test collection | PASS | default smoke path stable |
| Fast regression tests | PASS | 10 tests passed |
| Paper-mode single-cycle runtime | PASS | priming, inference, and simulated execution worked |
| Persistence / audit trail | PASS | execution / snapshot / pnl rows written |
| Runbook availability | PASS | startup / failure handling documented |
| Live-trading readiness | NO-GO | still requires stronger broker/live rehearsals and operator validation |

## Go / No-Go
### Paper trading
**GO** for continued paper-trading validation.

### Live trading
**NO-GO** for now.

Reasons:
1. `INFOWAY_API_KEY` not configured in environment
2. live-mode operator checks have not been rehearsed end-to-end
3. no explicit live broker rehearsal was performed in this sprint
4. some legacy tests remain excluded from default smoke lane and should be cleaned up later

## Important note from rehearsal
The single-cycle rehearsal still executed a BUY even though the temporary config set `auto_trade=false`.
That suggests a runtime behavior mismatch worth investigating before any stronger readiness claims. It does **not** block paper-mode experimentation, but it **does** block live-readiness confidence.

## Recommended next sprint
Priority order:
1. investigate / fix `auto_trade=false` behavior mismatch in the live loop
2. deepen execution audit trail to include more pre-execution decision context if desired
3. run a longer paper-trading soak test (multi-cycle / multi-symbol)
4. perform broker-connected rehearsal with explicit operator checklist
5. clean up legacy excluded tests

## Conclusion
The system has crossed from “fragile prototype” into “paper-trading-capable engineering system.”
That is real progress.

But the correct posture is still:
- **paper-ready enough to keep validating**
- **not yet live-ready enough to trust with real capital**
