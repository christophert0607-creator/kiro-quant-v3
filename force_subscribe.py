import futu as ft
import os

HK_SYMBOLS = [
    "HK.00700", "HK.09988", "HK.03690", "HK.01024", "HK.02318", "HK.01299", "HK.00939",
    "HK.00005", "HK.00388", "HK.00960", "HK.01109", "HK.00941", "HK.00175", "HK.01810",
    "HK.02688", "HK.02269", "HK.01211", "HK.02018", "HK.00688"
]

TOP_PRECOMPUTE_US = [
    "US.AAPL","US.MSFT","US.GOOGL","US.AMZN","US.NVDA","US.META","US.TSLA","US.AMD","US.AVGO","US.ORCL",
    "US.CRM","US.ADBE","US.CSCO","US.ACN","US.IBM","US.INTC","US.QCOM","US.TXN","US.AMAT","US.MU",
    "US.NFLX","US.COIN","US.MSTR","US.UBER","US.DASH","US.SNOW","US.PANW","US.CRWD","US.ZS","US.NET"
]

def force_subscribe():
    print("--- Emergency Force Subscription ---")
    quote_ctx = ft.OpenQuoteContext(host='127.0.0.1', port=11112)
    
    all_codes = HK_SYMBOLS + TOP_PRECOMPUTE_US
    
    ret, data = quote_ctx.subscribe(all_codes, [ft.SubType.QUOTE], subscribe_push=True)
    if ret == ft.RET_OK:
        print(f"Successfully subscribed to {len(all_codes)} symbols (HK + US)")
    else:
        print(f"Subscription failed: {data}")
    
    quote_ctx.close()

if __name__ == "__main__":
    force_subscribe()
