#!/bin/bash
# Kiro Quant 模拟仓重置脚本
# 每天交易时段结束后执行 - 按实时现价平仓

cd /home/tsukii0607/.openclaw/workspace/skills/kiro-quant

TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_DIR="backups/paper_trading_$TIMESTAMP"

# 创建备份目录
mkdir -p "$BACKUP_DIR"

# 按实时现价平仓的函数
python3 << 'PYTHON_SCRIPT'
import json
import requests
from datetime import datetime

def get_realtime_price(symbol):
    """获取实时价格"""
    # 移除 .HK 后缀获取正确格式
    if symbol.endswith('.HK'):
        symbol = symbol.replace('.HK', '') + '.HK'
    
    try:
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()
        price = data['chart']['result'][0]['meta']['regularMarketPrice']
        return price
    except Exception as e:
        print(f"获取 {symbol} 价格失败: {e}")
        return None

# 读取当前状态
with open('paper_trading_pnl.json') as f:
    data = json.load(f)

# 分析
analysis = []
analysis.append('=== 交易时段分析 (按实时现价平仓) ===')
analysis.append(f'时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
analysis.append('')

# 计算按实时现价平仓的盈亏
positions = data.get('positions', {})
total_realized_pnl = 0.0
total_market_value = 0.0

analysis.append('--- 账户状态 (重置前) ---')
analysis.append(f'起始资金: $100,000.00')
analysis.append(f'现金: ${data.get("cash", 0):.2f}')

if positions:
    analysis.append('')
    analysis.append('--- 平仓记录 (实时现价) ---')
    
    for sym, pos in positions.items():
        qty = pos.get('qty', 0)
        cost = pos.get('avg_cost', 0)
        
        # 获取实时现价
        realtime_price = get_realtime_price(sym)
        if realtime_price is None:
            realtime_price = pos.get('mark_price', cost)  # 备用
        
        cost_basis = qty * cost
        market_value = qty * realtime_price
        realized_pnl = market_value - cost_basis
        
        total_realized_pnl += realized_pnl
        total_market_value += market_value
        
        analysis.append(f'股票: {sym}')
        analysis.append(f'  数量: {qty}')
        analysis.append(f'  成本: ${cost:.2f}')
        analysis.append(f'  实时现价: ${realtime_price:.2f} ← 最新!')
        analysis.append(f'  买入时间: {pos.get("entry_time", "N/A")}')
        analysis.append(f'  成本总额: ${cost_basis:.2f}')
        analysis.append(f'  现值总额: ${market_value:.2f}')
        analysis.append(f'  实现盈亏: ${realized_pnl:.2f}')
        analysis.append('')
else:
    analysis.append('无持仓')

# 计算重置后的状态
new_cash = data.get('cash', 0) + total_market_value
new_equity = new_cash

analysis.append('--- 平仓后账户状态 ---')
analysis.append(f'平仓现金: ${total_market_value:.2f}')
analysis.append(f'实现盈亏: ${total_realized_pnl:.2f}')
analysis.append(f'新现金总额: ${new_cash:.2f}')

# 保存分析报告
with open(f'{BACKUP_DIR}/trading_analysis.txt', 'w') as f:
    f.write('\n'.join(analysis))

# 保存完整的PNL记录
with open(f'{BACKUP_DIR}/pnl_before_reset.json', 'w') as f:
    json.dump(data, f, indent=2)

# 创建新的模拟仓状态 (已平仓)
new_state = {
    'timestamp': datetime.utcnow().isoformat() + '+00:00',
    'cash': new_cash,
    'equity': new_equity,
    'realized_pnl': total_realized_pnl,
    'unrealized_pnl': 0.0,
    'net_pnl': total_realized_pnl,
    'positions': {}
}

with open('paper_trading_pnl.json', 'w') as f:
    json.dump(new_state, f, indent=2)

print('✅ 按实时现价平仓完成!')
print(f'实现盈亏: ${total_realized_pnl:.2f}')
print(f'新现金: ${new_cash:.2f}')
PYTHON_SCRIPT

# 通知 Telegram
TOKEN=$(cat .telegram_bot_token 2>/dev/null)
if [ -n "$TOKEN" ]; then
    curl -s -X POST "https://api.telegram.org/bot$TOKEN/sendMessage" \
        -d "chat_id=625655860" \
        -d "text=🔄 模擬倉已按實時現價平倉！

備份: $BACKUP_DIR
新現金: \$100,000

開始新交易時段！" > /dev/null
fi

echo "✅ 完成!"
