# Kiro Quant Runbook

## Runtime modes
- **Dry-run**: safest validation mode; no trade execution
- **Paper trading**: simulated execution with local state / PnL persistence
- **Live trading**: requires broker connectivity, trade password, and explicit operator review

## Standard startup sequence
1. Go to repo root:
   ```bash
   cd ~/.openclaw/workspace/skills/kiro-quant
   ```
2. Validate config:
   ```bash
   python3 validate_config.py --config config.json
   ```
3. Run preflight:
   ```bash
   python3 preflight.py --config config.json
   ```
4. Run dry-run smoke if needed:
   ```bash
   python3 v3_launcher.py --dry-run
   python3 v3_launcher.py --dry-run --profile standard
   ```
5. Start runtime:
   ```bash
   python3 v3_launcher.py
   ```

## When startup should be blocked
Do **not** start live trading if any of these are true:
- `preflight.py --strict` fails
- `Futu OpenD` is unreachable
- `FUTU_TRADE_PASSWORD` / `FUTU_TRADE_PWD` is missing for live mode
- `INFOWAY_API_KEY` is missing and you are depending on production-style market data
- config validation fails

## Paper-trading operations
### Inspect state
```bash
cat paper_trading_pnl.json
cat pnl_report.json
cat health.json
```

### Reset paper-trading account
```bash
./reset_paper_trading.sh
```
This creates a backup before resetting positions.

## Failure handling
### 1) Config / preflight failure
- Fix `config.json` or environment variables
- Re-run:
  ```bash
  python3 validate_config.py --config config.json
  python3 preflight.py --config config.json
  ```

### 2) Broker unreachable
- Check OpenD process and port `11111`
- Review `FUTU_OPERATIONS.md`
- In paper mode, broker-unreachable can be tolerated with fallback data, but should still be noted

### 3) Repeated execution / heartbeat errors
Inspect:
```bash
cat health.json
python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('kiro_quant.db')
for table in ['risk_events', 'alerts', 'executions', 'pnl_snapshots']:
    print('\nTABLE', table)
    for row in conn.execute(f'SELECT * FROM {table} ORDER BY id DESC LIMIT 5'):
        print(row)
conn.close()
PY
```

### 4) Emergency stop
If runtime is active in terminal / tmux, stop the process first. Then preserve evidence:
- `health.json`
- `paper_trading_pnl.json`
- `pnl_report.json`
- `kiro_quant.db`
- recent `logs/`

## Audit trail reference
Important runtime evidence is now persisted in SQLite:
- `executions`: filled trades
- `position_snapshots`: position snapshots over time
- `pnl_snapshots`: equity / pnl history
- `risk_events`: gate blocks, execution failures, and safety events
- `alerts`: outbound alert records

## Operator note
Hardcoded Infoway fallback keys are **disabled by default**.
If you deliberately want local-dev fallback behavior, set:
```bash
export KIRO_ALLOW_DEV_FALLBACK_KEYS=1
```
Do not use that in production-style runs.
