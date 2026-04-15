#!/bin/bash
export FUTU_OPEND_PORT=11112
export FUTU_PWD_UNLOCK=930607
cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3
python3 v3_launcher.py > v3_launcher.log 2>&1 &
echo "V3 started with PID $!"
