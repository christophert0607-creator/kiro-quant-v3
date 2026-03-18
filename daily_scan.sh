#!/bin/bash
# Daily Multi-Timeframe Stock Scan
# Run after data collection

echo "=== $(date) ===" 
echo "Running Multi-TF Stock Scan..."

cd ~/.openclaw/workspace/skills/kiro-quant/backtest_engine
python3 src/analysis/multi_tf_model.py 2>&1

echo "Scan complete."
