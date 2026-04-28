import yfinance as yf
import time

def check_us_prices():
    symbols = ['ZS', 'MDB', 'GOOGL', 'INTC', 'QCOM', 'MSFT', 'ACN', 'AVGO', 'TXN']
    print(f"Auditing real-time prices for US positions: {symbols}")
    try:
        # Use yfinance for quick check
        tickers = yf.Tickers(" ".join(symbols))
        for sym in symbols:
            price = tickers.tickers[sym].fast_info['last_price']
            print(f"SYMBOL: {sym} | LAST: {price:.2f}")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    check_us_prices()
