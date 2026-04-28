from futu import *
import time

def test_futu_lib():
    quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11112)
    print("Attempting to connect via futu library...")
    try:
        # Give it some time
        time.sleep(5)
        market_state = quote_ctx.get_global_state()
        print(f"Market state: {market_state}")
        quote_ctx.close()
    except Exception as e:
        print(f"Futu library connection failed: {e}")

if __name__ == "__main__":
    test_futu_lib()
