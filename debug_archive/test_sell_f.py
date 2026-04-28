from futu import *
import sys

# Constants
OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111
TRADE_PWD  = "930607"
CODE       = "US.F"

def test_sell():
    # 1. Connect
    trd_ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host=OPEND_HOST, port=OPEND_PORT, security_firm=SecurityFirm.FUTUSECURITIES)
    quote_ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
    
    try:
        # 2. Unlock
        ret, data = trd_ctx.unlock_trade(TRADE_PWD)
        if ret != RET_OK:
            print(f"Unlock failed: {data}")
            return

        # 3. Get Current Price
        ret, data = quote_ctx.get_stock_quote([CODE])
        if ret != RET_OK:
            print(f"Get quote failed: {data}")
            return
        
        cur_price = data.iloc[0]['last_price']
        print(f"Current price of {CODE}: {cur_price}")

        # 4. Place Order (SIMULATE)
        # Note: We use NORMAL (Limit Order) at current price for simulation reliability
        print(f"Placing SIMULATE SELL order for 1 share of {CODE} at {cur_price}...")
        ret, data = trd_ctx.place_order(price=cur_price, qty=1, code=CODE, trd_side=TrdSide.SELL, order_type=OrderType.NORMAL, trd_env=TrdEnv.SIMULATE)
        
        if ret == RET_OK:
            print("Successfully placed order!")
            print(data)
        else:
            print(f"Order failed: {data}")

    finally:
        trd_ctx.close()
        quote_ctx.close()

if __name__ == "__main__":
    test_sell()
