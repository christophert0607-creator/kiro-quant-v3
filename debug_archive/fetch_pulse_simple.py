import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

def get_pulse():
    symbols = ["2800.HK", "3033.HK"]
    end = datetime.now()
    start = end - timedelta(days=5)
    
    data = {}
    for sym in symbols:
        ticker = yf.Ticker(sym)
        df = ticker.history(period="5d", interval="30m")
        if not df.empty:
            last_price = df['Close'].iloc[-1]
            prev_price = df['Close'].iloc[-2] if len(df) > 1 else last_price
            change_30m = (last_price - prev_price) / prev_price
            
            # Day change
            today_start = df.index[-1].replace(hour=9, minute=30)
            day_df = df[df.index >= today_start]
            if not day_df.empty:
                day_open = day_df['Open'].iloc[0]
                day_change = (last_price - day_open) / day_open
            else:
                day_change = 0
                
            data[sym] = {
                "price": round(last_price, 3),
                "change_30m": round(change_30m * 100, 3),
                "day_change": round(day_change * 100, 3)
            }
    return data

if __name__ == "__main__":
    pulse = get_pulse()
    print(pulse)
