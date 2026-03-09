from futu import *
import sys

# Constants
OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111
TRADE_PWD  = "930607"

def check_orders():
    trd_ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, host=OPEND_HOST, port=OPEND_PORT, security_firm=SecurityFirm.FUTUSECURITIES)
    
    try:
        trd_ctx.unlock_trade(TRADE_PWD)
        
        # Query orders for TODAY
        print("Querying today's REAL orders...")
        ret, data = trd_ctx.order_list_query(trd_env=TrdEnv.REAL)
        
        if ret == RET_OK:
            if len(data) > 0:
                print("Order History (Last 5):")
                # Show relevant columns: code, trd_side, qty, price, order_status, create_time
                cols = ['code', 'trd_side', 'qty', 'dealt_qty', 'order_status', 'create_time', 'remark']
                print(data[cols].tail(5))
            else:
                print("No orders found today.")
        else:
            print(f"Failed to query orders: {data}")

    finally:
        trd_ctx.close()

if __name__ == "__main__":
    check_orders()
