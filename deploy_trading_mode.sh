#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/workspace/kiro-quant-v3"
cd "$REPO_DIR"

echo "[1/4] Stopping current v3_launcher..."
pkill -f "python.*v3_launcher.py" || true

echo "[2/4] Updating config.json for paper auto-trading..."
python - <<'PY'
import json
from pathlib import Path
p = Path('config.json')
cfg = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
cfg['auto_trade'] = True
cfg['paper_trading'] = True
cfg['v3_live'] = {
    'symbols_list': ['TSLA', 'TSLL', 'NVDA'],
    'prediction_thresholds': {'TSLA': 0.01, 'TSLL': 0.01, 'NVDA': 0.01},
    'buy_cooldown_cycles': 3,
    'polling_seconds': 15,
    'auto_trade': True,
    'paper_trading': True,
}
p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('config.json updated')
PY

echo "[3/4] Restarting launcher..."
nohup python v3_launcher.py > v3_pipeline/logs/v3_launcher.out 2>&1 &

echo "[4/4] Done. tail -f v3_pipeline/logs/v3_launcher.out"
