import json

try:
    import yfinance as yf
except ModuleNotFoundError:  # pragma: no cover - optional dependency for manual script
    yf = None

from datetime import datetime

symbol = "TSLA"

def get_realtime_vibe(ticker):
    print(f"📡 Scanning {ticker} via Yahoo Radar...")
    if yf is None:
        return {"error": "yfinance not installed"}

    try:
        t = yf.Ticker(ticker)
        # 獲取最近一天的 1 分鐘線 (這是最接近即時的免費數據)
        data = t.history(period="1d", interval="1m")
        
        if not data.empty:
            latest = data.iloc[-1]
            return {
                "symbol": ticker,
                "price": round(float(latest['Close']), 2),
                "change": round(float(latest['Close'] - data.iloc[0]['Open']), 2),
                "high": round(float(latest['High']), 2),
                "low": round(float(latest['Low']), 2),
                "volume": int(latest['Volume']),
                "time": str(data.index[-1]),
                "source": "yfinance (Yahoo)"
            }
    except Exception as e:
        return {"error": str(e)}
    return {"error": "No data found"}

if __name__ == "__main__":
    result = get_realtime_vibe(symbol)
    print(json.dumps(result, indent=2))
