import yfinance as yf
import time

def check_us_portfolio():
    # Full list of active US positions
    symbols = ['ZS', 'MDB', 'GOOGL', 'MSFT', 'INTC', 'QCOM', 'ACN', 'AVGO', 'TXN', 'IBM', 'MU', 'ADBE']
    print(f"Auditing real-time PnL for US portfolio: {symbols}")
    try:
        tickers = yf.Tickers(" ".join(symbols))
        for sym in symbols:
            price = tickers.tickers[sym].fast_info['last_price']
            print(f"SYMBOL: {sym} | LAST: {price:.2f}")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    check_us_portfolio()
