#!/usr/bin/env python3
"""
手動觸發一組 BUY + SELL 訂單，用於收集今日買賣數據。
使用 Futu SIMULATE 模式，不影響真實帳戶。
"""
import sys
sys.path.insert(0, '.')

from v3_pipeline.core.futu_connector import FutuConnector, FutuConfig
from self_learn import hook_on_signal
import time

config = FutuConfig(
    host='127.0.0.1',
    port=11112,
    trd_env='SIMULATE'
)

fc = FutuConnector(config=config)

print("連接 Futu OpenD (SIMULATE mode)...")
fc.connect()
time.sleep(1)

# 先用 1 股測試 BUY
symbol = 'NVDA'
buy_qty = 1

# 取得實時價格（通過 yfinance）
import yfinance as yf
data = yf.download(symbol, period="1d", interval="1m", progress=False)
ref_price = float(data['Close'].iloc[-1])
ref_price = round(ref_price, 2)
print(f"\n{symbol} 參考價格: ${ref_price}")

print(f"\n=== STEP 1: BUY {buy_qty} 股 {symbol} @ ${ref_price} ===")
buy_res = fc.place_order(symbol=symbol, qty=buy_qty, side='BUY', price=ref_price)
print(f"BUY result: {buy_res}")
time.sleep(2)

# 檢查持倉
pos = fc.get_sync_positions()
print(f"\n持倉狀態:\n{pos}")

# 立即 SELL 相同數量
sell_price = ref_price  # Use same price for immediate sell
print(f"\n=== STEP 2: SELL {buy_qty} 股 {symbol} @ ${sell_price} ===")
sell_res = fc.place_order(symbol=symbol, qty=buy_qty, side='SELL', price=sell_price)
print(f"SELL result: {sell_res}")
time.sleep(2)

# 最終持倉
pos2 = fc.get_sync_positions()
print(f"\n最終持倉狀態:\n{pos2 if pos2 is not None else '(空)'}")

# 資產
assets = fc.get_sync_assets()
print(f"\n帳戶資產:\n{assets}")

fc.close()
print("\n完成！")
