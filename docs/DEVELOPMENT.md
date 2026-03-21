# Kiro Quant Development Guide

## Canonical local setup
Use Python 3.12+ (3.14 also works in the current workspace, but 3.12 is the safer baseline).

```bash
cd ~/.openclaw/workspace/skills/kiro-quant
python3 -m pip install -r requirements-dev.txt
```

If your distro enforces PEP 668, use:

```bash
PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install -r requirements-dev.txt
```

## Standard validation commands
### 1) Config validation
```bash
python3 validate_config.py --config config.json
```

### 2) Preflight safety check
```bash
python3 preflight.py --config config.json
```

### 3) Compile smoke
```bash
python3 -m compileall v3_launcher.py v3_pipeline
```

### 4) Dry-run smoke
```bash
python3 v3_launcher.py --dry-run
python3 v3_launcher.py --dry-run --profile standard
```

### 5) Test discovery / fast tests
```bash
python3 -m pytest --collect-only
python3 -m pytest tests/test_risk_manager.py tests/test_state_store_timestamp.py
```

Default discovery intentionally ignores three legacy/broken tests for now:
- `tests/test_main_loop_trade_bridge.py` (syntax / indentation issue)
- `tests/test_quant_v2_kline_cache.py` (imports missing legacy module `quant_v2`)
- `tests/test_risk_guard_net_assets.py` (imports missing legacy module `risk_guard`)

Those should be either repaired or migrated in a later cleanup pass instead of breaking smoke CI.

## Phase 1 conventions
- `config.json` is the active runtime config.
- `config.example.json` is the template and must stay aligned with the active schema.
- Keep generated logs, backups, local DB snapshots, and temporary artifacts out of normal source control flow.
- Use `validate_config.py` in CI before dry-run smoke checks.

## Phase 2 conventions
- Run `preflight.py` before non-dry-run starts.
- Hardcoded Infoway fallback keys are disabled by default.
- If you intentionally want local-dev fallback behavior, set `KIRO_ALLOW_DEV_FALLBACK_KEYS=1` explicitly.
- SQLite now tracks `executions`, `position_snapshots`, `pnl_snapshots`, `risk_events`, and `alerts` in addition to `market_data`.

## Test categorization guidance
- `unit`: pure logic / dependency-free tests
- `integration`: broader runtime behavior with multiple components wired together
- `broker`: OpenD / broker-context dependent tests
- `slow`: heavier tests not required for smoke CI

Current `pytest.ini` declares these markers. As tests evolve, add markers explicitly where appropriate.
