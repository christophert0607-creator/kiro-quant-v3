import yfinance as yf
import json
from datetime import datetime, timedelta

def get_data():
    symbols = ['2800.HK', '3033.HK', '0700.HK', '9988.HK', '3690.HK']
    data = {}
    for s in symbols:
        ticker = yf.Ticker(s)
        hist = ticker.history(period='5d', interval='30m')
        if not hist.empty:
            data[s] = {
                'current': hist['Close'].iloc[-1],
                'prev_close': hist['Close'].iloc[-2] if len(hist) > 1 else hist['Close'].iloc[-1],
                'change_pct': ((hist['Close'].iloc[-1] / hist['Close'].iloc[-2]) - 1) * 100 if len(hist) > 1 else 0,
                'momentum_30m': hist['Close'].pct_change().iloc[-1] * 100 if len(hist) > 1 else 0
            }
    return data

if __name__ == "__main__":
    print(json.dumps(get_data()))
