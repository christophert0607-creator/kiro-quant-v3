from futu import *
import sys

def test_us_kline():
    quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
    try:
        print("Testing US.TSLA K-line fetching...")
        ret, data, _ = quote_ctx.request_history_kline("US.TSLA", ktype=KLType.K_DAY, max_count=10)
        if ret == RET_OK:
            print("Successfully fetched TSLA K-lines:")
            print(data)
        else:
            print(f"Failed to fetch TSLA K-lines: {data}")
    finally:
        quote_ctx.close()

if __name__ == "__main__":
    test_us_kline()
