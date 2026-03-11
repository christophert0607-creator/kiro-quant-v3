# 7x24 Quant Trading Script

## Run
```bash
python scripts/quant_7x24_us_trader.py --config scripts/quant_7x24_config.example.json
```

## Deployment (systemd)
1. Copy config to a secure path and fill API keys.
2. Create a systemd service pointing to the command above.
3. Enable auto-restart (`Restart=always`) for unattended operation.

## Notes
- `trading.dry_run=true` keeps the script in paper/simulation mode.
- CUDA training is used when torch+CUDA are available.
