# V3 Agent Workflow Reference

## 1) Intake
- Confirm target symbols and environment (`paper_trading`, `auto_trade`)
- Validate `v3_live` keys in config

## 2) Lightweight-first execution
1. `python v3_launcher.py --dry-run`
2. `python v3_launcher.py --dry-run --profile standard` (optional comparison)
3. `python -m compileall v3_launcher.py v3_pipeline`

If all green, then decide runtime:
- **lite**: low-resource /巡檢
- **standard**: normal production simulation

## 3) Runtime switch matrix
- config-based: set `v3_live.runtime_profile`
- CLI override: `--profile lite|standard`

CLI override takes precedence over config.

## 4) Operational checks
- Futu connection: ensure OpenD reachable (`127.0.0.1:11111`)
- Data fallback sanity: FUTU -> Massive -> yfinance
- Risk gate sanity: verify no change bypasses risk manager path

## 5) Progress tracking template
Use this checklist in `PROGRESS.md`:
- [ ] config validated
- [ ] dry-run passed
- [ ] compileall passed
- [ ] runtime selected and started
- [ ] docs updated

## 6) Handoff standard
In final handoff include:
- commands executed
- pass/fail status
- changed files
- recommended next steps
