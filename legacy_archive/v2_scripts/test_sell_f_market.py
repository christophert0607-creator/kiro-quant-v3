from futu import *
import sys

# Constants
OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111
TRADE_PWD  = "930607"
CODE       = "US.F"

def test_sell_market():
    # 1. Connect
    trd_ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host=OPEND_HOST, port=OPEND_PORT, security_firm=SecurityFirm.FUTUSECURITIES)
    
    try:
        # 2. Unlock
        ret, data = trd_ctx.unlock_trade(TRADE_PWD)
        if ret != RET_OK:
            print(f"Unlock failed: {data}")
            return

        # 4. Place Order (SIMULATE - MARKET ORDER)
        print(f"Placing SIMULATE MARKET SELL order for 1 share of {CODE}...")
        # Note: OrderType.MARKET is for US market
        ret, data = trd_ctx.place_order(price=0, qty=1, code=CODE, trd_side=TrdSide.SELL, order_type=OrderType.MARKET, trd_env=TrdEnv.SIMULATE)
        
        if ret == RET_OK:
            print("Successfully placed MARKET order!")
            print(data)
        else:
            print(f"Order failed: {data}")

    finally:
        trd_ctx.close()

if __name__ == "__main__":
    test_sell_market()
