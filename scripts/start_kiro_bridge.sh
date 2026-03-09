#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export KIRO_BRIDGE_HOST="${KIRO_BRIDGE_HOST:-0.0.0.0}"
export KIRO_BRIDGE_PORT="${KIRO_BRIDGE_PORT:-18888}"
export V3_LOG_PATH="${V3_LOG_PATH:-/home/tsukii0607/.openclaw/workspace/skills/futu-api/v3_pipeline/logs/v3_live_trade.log}"

python -m uvicorn kiro_bridge:app --host "$KIRO_BRIDGE_HOST" --port "$KIRO_BRIDGE_PORT"
