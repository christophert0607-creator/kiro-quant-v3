from futu import *
import time

def get_real_prices():
    quote_ctx = OpenQuoteContext(host='127.0.0.1', port=11112)
    symbols = ['HK.00960', 'HK.00939', 'HK.00688']
    print(f"Fetching last prices for: {symbols}")
    try:
        quote_ctx.subscribe(symbols, [SubType.QUOTE], subscribe_push=False)
        ret, df = quote_ctx.get_stock_quote(symbols)
        if ret == RET_OK:
            for _, row in df.iterrows():
                print(f"SYMBOL: {row['code']} | LAST: {row['last_price']}")
        else:
            print(f"ERROR: {df}")
        quote_ctx.close()
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    get_real_prices()
