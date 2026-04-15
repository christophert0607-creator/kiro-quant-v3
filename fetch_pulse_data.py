
import yfinance as yf
from datetime import datetime, timedelta

def get_data():
    tickers = ["2800.HK", "3033.HK", "0700.HK", "9988.HK", "3690.HK"]
    end = datetime.now()
    start = end - timedelta(days=5)
    
    print(f"Fetching data from {start} to {end}")
    for t in tickers:
        df = yf.download(t, start=start, end=end, interval="30m")
        if not df.empty:
            last_price = float(df['Close'].iloc[-1])
            prev_price = float(df['Close'].iloc[-2])
            change = (last_price - prev_price) / prev_price * 100
            print(f"{t}: Last={last_price:.2f}, Prev={prev_price:.2f}, 30m_Change={change:.2f}%")
            # Calculate 1-day change roughly from first bar of current/prev day
            # (Simplification for pulse)
            day_start_price = float(df['Open'].iloc[0])
            day_change = (last_price - day_start_price) / day_start_price * 100
            print(f"{t}: Day_Change={day_change:.2f}%")
        else:
            print(f"{t}: No data found")

if __name__ == "__main__":
    get_data()
