# Kiro Quant V3 Patch Notes

## 2026-04-28 - OpenClaw/Hermes Cron Hardening + Trading Logic Repair

Timezone: Asia/Hong_Kong  
Related plan: `OPENCLAW_HERMES_CRON_PLAN.md`

### Summary

This patch implements the OpenClaw/Hermes operating plan from the analysis report:

- Repair a P0 V3 trading-logic structural bug.
- Add automated guards so the same bug is caught before market sessions.
- Make OpenD host/port/account config-driven instead of hard-coded.
- Split OpenClaw and Hermes responsibilities to avoid overlapping restart behavior.
- Add cron jobs for structural lint, config audit, singleton/runtime watch, snapshots, and read-only heartbeat.

No live `v3_launcher.py` process was killed or restarted during this patch.

### Original Plan Implemented

| Plan item | Status | Notes |
| --- | --- | --- |
| Fix V3 trading logic indentation/scope issue | Done | Entry/exit gates are back inside `_run_trading_logic()`. |
| Add structural lint and runtime supervisor scripts | Done | Added AST guard, config audit, and read-only runtime supervisor. |
| Unify snapshot/health OpenD port source | Done | Scripts now read `kiro-quant-v3/config.json` where appropriate. |
| Update OpenClaw/Hermes cron definitions | Done | OpenClaw owns supervisor/watch; Hermes owns snapshot/pulse/research/read-only heartbeat. |
| Run verification | Done | Compile, structural lint, config audit, cron JSON validation, and read-only snapshot all passed. |

### Code Changes

#### `v3_pipeline/core/main_loop.py`

- Fixed `_get_market_thresholds()` being inserted in the middle of `_run_trading_logic()`.
- Restored the following trading sections to reachable `_run_trading_logic()` scope:
  - confidence gate
  - short confidence gate
  - short entry
  - long buy entry
  - sell confirmation
- Moved `_get_market_thresholds()` to class level after `_run_trading_logic()`.
- Replaced invalid `_execute_short_cover(...)` call with existing `_execute_short_exit(...)`.
- Removed one trailing-whitespace issue caught by `git diff --check`.

Structural verification after patch:

| Method | Line range | Expected trading markers |
| --- | --- | --- |
| `_run_trading_logic` | `525-1245` | Contains `CONF_GATE`, `SHORT_PLACED`, `BUY_PLACED`, `SELL_PLACED` |
| `_get_market_thresholds` | `1248-1265` | Contains none of the trading action markers |

#### New scripts in `/home/tsukii0607/.openclaw/workspace-quant/scripts`

| Script | Purpose |
| --- | --- |
| `kiro_v3_structural_lint.py` | AST/compile guard for unreachable V3 trading gates. |
| `kiro_v3_runtime_supervisor.py` | Read-only runtime guard for duplicate launchers, OpenD port, and stale runtime logs; optional `--start-if-dead`. |
| `kiro_v3_config_audit.py` | Conservative `config.json` sanity audit for Futu/OpenD and V3 live settings. |

#### Updated scripts

| Script | Change |
| --- | --- |
| `us_sim_snapshot_cron.py` | Reads `futu.host`, `futu.port`, and `futu.target_acc_id` from `kiro-quant-v3/config.json`. |
| `run_us_sim_snapshot.sh` | Calls `scripts/us_sim_snapshot_cron.py`. |
| `run_snapshot.sh` | Calls `scripts/us_sim_snapshot_cron.py`. |
| `health_check.py` | Reads active OpenD host/port from V3 `config.json`; OpenD auto-restart disabled unless `KIRO_HEALTH_AUTORESTART=1`. |
| `us_sim_snapshot.py` | Default port changed to the active V3 port. |
| `us_sim_quickcheck.py` | Default port changed to the active V3 port. |

### Cron Changes

Cron JSON backups were created before modification:

- `/home/tsukii0607/.openclaw/cron/jobs.json.bak_kiro_plan_20260428204832`
- `/home/tsukii0607/.hermes/cron/jobs.json.bak_kiro_plan_20260428204832`

#### OpenClaw role

OpenClaw now acts as the control-plane side:

| Job | Schedule HKT | Status | Purpose |
| --- | --- | --- | --- |
| `Kiro V3 HK Preopen Supervisor` | `25 9 * * 1-5` | Enabled | Runs structural lint, config audit, then starts only if dead. |
| `Kiro V3 Structural Lint` | `10 8 * * 1-5` | Enabled | Daily P0 AST guard before market. |
| `Kiro V3 Config Audit` | `5 9,20 * * 1-5` | Enabled | Pre-session config sanity check. |
| `Kiro V3 Singleton Guard` | `20 9,20 * * 1-5` | Enabled | Read-only duplicate launcher guard. |
| `Kiro V3 P0 Watch` | `*/15 9-16,20-23,0-4 * * 1-5` | Enabled | Market-hours runtime/OpenD/log freshness watch. |

OpenClaw cron prompts were also cleaned so they no longer include direct API key export instructions.

#### Hermes role

Hermes now acts as the analyst/operations side:

| Job | Status | Change |
| --- | --- | --- |
| `KiroQuant - US SIM Snapshot (5m, US hours)` | Enabled | Uses config-driven snapshot wrapper; no hard-coded old port. |
| `KiroQuant - V3 Heartbeat` | Enabled | Read-only only; no `pkill`, no restart. |
| `KiroQuant - HK Market Pulse (30m)` | Enabled | Targets active `config.json`, not old sim config. |
| `Kiro Quant V3 每日代碼審查` | Enabled | Adds structural lint/config audit as P0 checks. |
| HK/US pre-market checks | Enabled | Use runtime supervisor for engine health. |

### Verification

Commands run and results:

| Check | Result |
| --- | --- |
| `python3 -m py_compile` for changed Python files | Passed |
| `scripts/kiro_v3_structural_lint.py` | Passed |
| `scripts/kiro_v3_config_audit.py` | Passed |
| OpenClaw/Hermes cron JSON parse | Passed |
| `git diff --check` on `main_loop.py` | Passed |
| `scripts/us_sim_snapshot_cron.py` read-only snapshot | Passed; wrote a snapshot using active config port |

Read-only snapshot confirmed:

- OpenD host/port: from `config.json`
- Account: from `config.json`
- Output file: `/home/tsukii0607/.openclaw/workspace-quant/learning/us_sim/account_snap_us_sim.jsonl`

### Current Residual Risk

`kiro_v3_runtime_supervisor.py --quiet-ok` currently reports:

```text
KIRO_V3_RUNTIME P0 duplicate_v3_launcher pids=[19643, 23202]
```

This was intentionally not auto-fixed in this patch because killing/restarting a live trading process should be a deliberate operational action. The new cron guard will now surface this as a P0 instead of silently allowing duplicate engines.

Recommended next action:

- Before the next live session, perform a controlled dedupe and keep only one `v3_launcher.py` instance.
- After dedupe, rerun:

```bash
cd /home/tsukii0607/.openclaw/workspace-quant
python3 scripts/kiro_v3_runtime_supervisor.py --quiet-ok
```

Expected healthy output:

```text
NO_REPLY
```

### Files Changed In This Patch

| Path | Type |
| --- | --- |
| `v3_pipeline/core/main_loop.py` | Trading logic repair |
| `/home/tsukii0607/.openclaw/workspace-quant/scripts/kiro_v3_structural_lint.py` | New guard script |
| `/home/tsukii0607/.openclaw/workspace-quant/scripts/kiro_v3_runtime_supervisor.py` | New supervisor script |
| `/home/tsukii0607/.openclaw/workspace-quant/scripts/kiro_v3_config_audit.py` | New config audit script |
| `/home/tsukii0607/.openclaw/workspace-quant/scripts/us_sim_snapshot_cron.py` | Config-driven snapshot wrapper |
| `/home/tsukii0607/.openclaw/workspace-quant/scripts/run_us_sim_snapshot.sh` | Wrapper entrypoint update |
| `/home/tsukii0607/.openclaw/workspace-quant/scripts/run_snapshot.sh` | Wrapper entrypoint update |
| `/home/tsukii0607/.openclaw/workspace-quant/scripts/health_check.py` | Config-driven OpenD check |
| `/home/tsukii0607/.openclaw/workspace-quant/scripts/us_sim_snapshot.py` | Default port update |
| `/home/tsukii0607/.openclaw/workspace-quant/scripts/us_sim_quickcheck.py` | Default port update |
| `/home/tsukii0607/.openclaw/cron/jobs.json` | OpenClaw cron role update |
| `/home/tsukii0607/.hermes/cron/jobs.json` | Hermes cron role update |
