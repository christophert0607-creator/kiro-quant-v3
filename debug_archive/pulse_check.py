import yfinance as yf
import json
from datetime import datetime, timedelta

def get_pulse():
    tickers = ["2800.HK", "3033.HK", "0700.HK", "9988.HK", "3690.HK"]
    results = {}
    for t in tickers:
        try:
            # Get 2 days of 30m data
            df = yf.download(t, period="2d", interval="30m", progress=False)
            if not df.empty:
                last_price = df['Close'].iloc[-1]
                prev_close = df['Close'].iloc[0]
                change_pct = (last_price - prev_close) / prev_close
                
                # Simple momentum (last 3 bars)
                recent_change = (df['Close'].iloc[-1] - df['Close'].iloc[-4]) / df['Close'].iloc[-4] if len(df) >= 4 else 0
                
                results[t] = {
                    "price": float(last_price),
                    "2d_change": float(change_pct),
                    "30m_momentum": float(recent_change)
                }
        except Exception as e:
            results[t] = {"error": str(e)}
    
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    get_pulse()
