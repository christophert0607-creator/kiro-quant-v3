
import sys
import os
sys.path.insert(0, '.')
from v3_pipeline.core.futu_connector import FutuConnector, FutuConfig

config = FutuConfig(
    host='127.0.0.1',
    port=11112,
    trd_env='SIMULATE'
)

fc = FutuConnector(config=config)
try:
    print('Connecting to Futu OpenD...')
    fc.connect()
    
    test_symbols = ['NVDA', 'AAPL']
    
    for symbol in test_symbols:
        print(f'Placing test BUY order for {symbol}...')
        res = fc.place_order(symbol=symbol, qty=1, side='BUY')
        # Fix the ambiguous DataFrame check
        if res is not None:
            print(f'Successfully placed order for {symbol}')
            # print(res) # Optional: print the DataFrame
        else:
            print(f'Order for {symbol} returned None')
            
    print('\n--- Final State Check ---')
    assets = fc.get_sync_assets()
    print(f'Assets: {assets}')
    
    pos = fc.get_sync_positions()
    if pos is None or pos.empty:
        print('Positions: (empty)')
    else:
        print('Current Positions:')
        print(pos[['code', 'qty', 'cost_price', 'market_price']].to_string())

except Exception as e:
    print(f'Error during test trade: {e}')
    import traceback
    traceback.print_exc()
finally:
    fc.close()
