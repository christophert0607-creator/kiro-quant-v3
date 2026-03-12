import yfinance as yf
import pandas as pd
from pathlib import Path
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("DataFetcher")

US_STOCKS = [
    "TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "GOOG", "META", "NFLX", "AMD", 
    "QCOM", "INTC", "SPY", "QQQ", "AVGO", "SMCI", "ARM", "PLTR", "SOXL"
]
HK_STOCKS = [
    "0700.HK", "9988.HK", "2800.HK", "3690.HK", "0388.HK", "1299.HK", "0941.HK", 
    "0883.HK", "0005.HK", "1810.HK", "2018.HK", "9999.HK", "9618.HK", "1753.HK"
]

def fetch_symbol(symbol: str, output_dir: Path, start_date: str) -> None:
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start_date)
        if df.empty:
            logger.warning(f"No data for {symbol}")
            return
        
        df = df.reset_index()
        # Ensure correct symbol prefix standard
        filename_sym = symbol.replace(".HK", "") if ".HK" in symbol else symbol
        
        out_path = output_dir / f"{filename_sym}_1d.parquet"
        
        # Ensure timezone-naive Date column as trainer expects
        if "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"], utc=True).dt.tz_localize(None)
        elif "Datetime" in df.columns:
            df["Date"] = pd.to_datetime(df["Datetime"], utc=True).dt.tz_localize(None)
            df.drop(columns=["Datetime"], inplace=True)
            
        df.to_parquet(out_path, index=False)
        logger.info(f"✅ Saved {symbol} -> {out_path} ({len(df)} rows)")
    except Exception as e:
        logger.error(f"❌ Error fetching {symbol}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Fetch historical data for 50-100 stocks")
    parser.add_argument("--output-dir", type=str, default="v3_pipeline/data/storage/base_20y")
    parser.add_argument("--start", type=str, default="2010-01-01")
    args = parser.parse_args()
    
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    symbols = US_STOCKS + HK_STOCKS
    logger.info(f"Starting batch fetch for {len(symbols)} symbols...")
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        for sym in symbols:
            executor.submit(fetch_symbol, sym, out_dir, args.start)
            
    logger.info("🎉 Batch fetch complete.")

if __name__ == "__main__":
    main()
