#!/bin/bash
# Kiro Quant 每日分析腳本
# 每天 06:00 執行 (香港時間)
# 分析昨日數據，識別優化點，記錄到本地和 GitHub

cd /home/tsukii0607/.openclaw/workspace/skills/kiro-quant

DATE=$(date -d '1 day ago' '+%Y-%m-%d')
TIMESTAMP=$(date -d '1 day ago' '+%Y%m%d')
LOCAL_DIR="/home/tsukii0607/.openclaw/workspace/kiro_quant_daily"

mkdir -p "$LOCAL_DIR"

echo "=== 開始每日分析 ($DATE) ==="

# 獲取備份目錄
LATEST_BACKUP=$(ls -td backups/paper_trading_* 2>/dev/null | head -1)

if [ -z "$LATEST_BACKUP" ]; then
    echo "沒有找到備份數據"
    exit 1
fi

# 分析昨日交易
python3 << PYTHON
import json
import os
from datetime import datetime, timedelta

# 找最新的備份
backups = sorted([d for d in os.listdir('backups') if d.startswith('paper_trading_')])
if not backups:
    print("無備份")
    exit()

latest = backups[-1]
path = f'backups/{latest}'

# 讀取數據
with open(f'{path}/pnl_before_reset.json') as f:
    data = json.load(f)

# 統計
cash = data.get('cash', 0)
equity = data.get('equity', 0)
realized = data.get('realized_pnl', 0)
unrealized = data.get('unrealized_pnl', 0)

# 持倉
positions = data.get('positions', {})
position_list = []
if positions:
    for sym, pos in positions.items():
        position_list.append({
            'symbol': sym,
            'qty': pos.get('qty', 0),
            'cost': pos.get('avg_cost', 0),
            'entry': pos.get('entry_time', '')
        })

# 寫入本地記錄 (JSON)
local_record = {
    'date': '$DATE',
    'cash': cash,
    'equity': equity,
    'realized_pnl': realized,
    'unrealized_pnl': unrealized,
    'positions': position_list
}

local_path = '$LOCAL_DIR/record_$DATE.json'
with open(local_path, 'w') as f:
    json.dump(local_record, f, indent=2)

# 寫入本地 Markdown 報告
md_content = f"""# 📊 Kiro Quant 每日報告 - {datetime.now().strftime('%Y-%m-%d')}

## 帳戶狀態
| 項目 | 數值 |
|------|------|
| 起始資金 | $100,000 |
| 結束現金 | ${cash:.2f} |
| 實現盈虧 | ${realized:.2f} |
| 未實現盈虧 | ${unrealized:.2f} |

## 持倉記錄
"""

if position_list:
    for p in position_list:
        md_content += f"- {p['symbol']}: {p['qty']}股 @ ${p['cost']:.2f}\n"
else:
    md_content += "- 無持倉\n"

md_content += f"""
## 問題記錄
(待分析)

---
生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

md_path = '$LOCAL_DIR/report_$DATE.md'
with open(md_path, 'w') as f:
    f.write(md_content)

print(f'記錄已保存: {local_path}')
print(f'報告已保存: {md_path}')
PYTHON

# 識別優化點並記錄
python3 << 'PYTHON'
import json
import os
from datetime import datetime

# 讀取昨日數據
backups = sorted([d for d in os.listdir('backups') if d.startswith('paper_trading_')])
if not backups:
    exit()

latest = backups[-1]
path = f'backups/{latest}'

try:
    with open(f'{path}/trading_analysis.txt') as f:
        content = f.read()
except:
    content = ""

# 識別問題
issues = []

# 檢查交易次數
if 'BUY' not in content and 'SELL' not in content:
    issues.append("無任何交易，可能是 ROR_GATE 阻擋")

if content.count('SELL') == 0:
    issues.append("沒有賣出，可能止盈/止損未觸發")

# 寫入優化記錄
opt_path = '$LOCAL_DIR/optimization_$DATE.md'

opt_content = f"""# 🎯 優化建議 - {datetime.now().strftime('%Y-%m-%d')}

## 發現的問題
"""

if issues:
    for issue in issues:
        opt_content += f"- {issue}\n"
else:
    opt_content += "- 系統運行正常\n"

opt_content += """
## 建議優化
1. 檢查 ROR_GATE 閾值
2. 驗證止盈/止損益邏輯
3. 添加持倉分散邏輯
"""

with open(opt_path, 'w') as f:
    f.write(opt_content)

print(f'優化建議已保存: {opt_path}')
PYTHON

# 發送到 Telegram
TOKEN=$(cat .telegram_bot_token 2>/dev/null)
if [ -n "$TOKEN" ]; then
    curl -s -X POST "https://api.telegram.org/bot$TOKEN/sendMessage" \
        -d "chat_id=625655860" \
        -d "text=📊 每日分析完成！

記錄已保存到本地:
- $LOCAL_DIR/record_$DATE.json
- $LOCAL_DIR/report_$DATE.md" > /dev/null
fi

echo "=== 每日分析完成 ==="
echo "本地記錄: $LOCAL_DIR/"
