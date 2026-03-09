from futu import *
import sys

# Connect to trading context (HK market)
# Need unlock trade first
pwd_unlock = '930607'

try:
    # 1. Open Context
    trd_ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.HK, host='127.0.0.1', port=11111, security_firm=SecurityFirm.FUTUSECURITIES)
    
    # 2. Unlock Trade (if needed, usually auto-unlocked on login but good practice)
    ret, data = trd_ctx.unlock_trade(pwd_unlock)
    if ret != RET_OK:
        print('Unlock trade failed: ', data)
    else:
        print('Unlock trade success.')
        
    # 3. Get Position List
    ret, data = trd_ctx.position_list_query()
    if ret == RET_OK:
        print('Position List:')
        print(data)
        if len(data) > 0:
             print("\n--- Summary ---")
             for index, row in data.iterrows():
                 print(f"Stock: {row['stock_name']} ({row['code']})")
                 print(f"  Qty: {row['qty']}")
                 print(f"  Cost: {row['cost_price']}")
                 print(f"  Current: {row['market_val']}")
                 print(f"  PL: {row['pl_val']} ({row['pl_ratio']}%)")
                 print("---------------")
        else:
            print("No positions found.")
    else:
        print('Position query failed: ', data)

    trd_ctx.close()
except Exception as e:
    print(f"Error: {e}")
