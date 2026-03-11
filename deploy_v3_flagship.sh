#!/usr/bin/env bash
set -euo pipefail

# 💖 Kiro-Quant V3.5 旗艦級部署腳本 (元氣修正版)
REPO_DIR="/home/tsukii0607/.openclaw/workspace/skills/futu-api"
cd "$REPO_DIR"

VENV_PYTHON="./v3_venv/bin/python3"

echo "[1/4] 🚀 正在清空舊的發動機進程..."
pkill -f "v3_launcher.py" || true
pkill -9 -f "v3_launcher.py" || true

echo "[2/4] ⚙️ 正在重新注入 V3.5 全自動模擬交易配置..."
$VENV_PYTHON - <<'PY'
import json
from pathlib import Path
p = Path('config.json')
cfg = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
# 開啟獵殺開關！
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
print('✅ config.json 獵殺配置已更新！')
PY

echo "[3/4] 🔥 旗艦引擎重新點火 (V3.5 Extended)..."
export PYTHONPATH=$REPO_DIR
# 使用 nohup 讓引擎在背景 24/7 燃燒！
nohup $VENV_PYTHON v3_launcher.py > v3_pipeline/logs/v3_launcher.out 2>&1 &

echo "[4/4] ✨ 部署完成！111艦隊（TSLA/TSLL/NVDA）正以 15s 頻率執行獵殺！"
echo "📊 指揮官請查看: v3_pipeline/logs/v3_launcher.out"
