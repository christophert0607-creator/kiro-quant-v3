#!/usr/bin/env bash
set -euo pipefail

OPEND_DIR="/home/tsukii0607/futu/Futu_OpenD_10.0/Futu_OpenD_10.0.6018_Ubuntu18.04/Futu_OpenD_10.0.6018_Ubuntu18.04"
LOG="/home/tsukii0607/.openclaw/workspace-quant/futu_opend_11112.log"

mkdir -p "$(dirname "$LOG")"

# Kill any existing OpenD
pkill -f "FutuOpenD" || true
sleep 1

cd "$OPEND_DIR"
nohup ./FutuOpenD > "$LOG" 2>&1 &

sleep 2

echo "✅ FutuOpenD started"
ss -tlnp | grep 11112 || true
