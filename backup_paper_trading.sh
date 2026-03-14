#!/bin/bash
# Auto backup paper trading data daily

DATE=$(date +%Y-%m-%d)
TIME=$(date +%H%M%S)
SOURCE_DIR="/home/tsukii0607/.openclaw/workspace/skills/kiro-quant"
BACKUP_DIR="/home/tsukii0607/.openclaw/workspace/kiro_quant_daily/paper_trading"

# Check if paper_trading_pnl.json exists
if [ -f "$SOURCE_DIR/paper_trading_pnl.json" ]; then
    # Copy with timestamp
    cp "$SOURCE_DIR/paper_trading_pnl.json" "$BACKUP_DIR/${DATE}_${TIME}.json"
    
    # Create summary
    python3 << PYEOF
import json
import os

data = json.load(open("$SOURCE_DIR/paper_trading_pnl.json"))
summary = f"""# {DATE} Paper Trading Summary

## Account
- Cash: \${data.get('cash', 0):.2f}
- Equity: \${data.get('equity', 0):.2f}
- Total PnL: \${data.get('net_pnl', 0):.2f}

## Positions
"""

positions = data.get('positions', {})
if positions:
    for symbol, pos in positions.items():
        summary += f"""### {symbol}
- Qty: {pos.get('qty', 0)}
- Avg Cost: \${pos.get('avg_cost', 0):.2f}
- Mark Price: \${pos.get('mark_price', 0):.2f}
- Unrealized PnL: \${pos.get('unrealized_pnl', 0):.2f}

"""
else:
    summary += "No positions\n"

with open(f"$BACKUP_DIR/{DATE}_summary.md", "w") as f:
    f.write(summary)

print(f"Backup saved: {DATE}")
PYEOF
fi
