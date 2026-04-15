#!/usr/bin/env python3
"""Download missing stock data to parquet during IDLE hours."""
import yfinance as yf
import pandas as pd
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys

STORAGE = Path(__file__).parent / "v3_pipeline" / "data" / "storage" / "base_20y"

MISSING_HK = [
    '0960','0688','1024','2318','0939','0011','0823','1099','1113','1681',
    '1772','1929','1972','2007','2098','2138','2158','2282','2313','2319',
    '2330','2382','2628','2688','2698','2828','2888','3319','3328','3688',
    '3888','3969','3988','6030','6618','6690','6837','8236','9633','9688',
    '9826','9886','9961'
]

MISSING_US = [
    'ABBV','ABT','ACN','ADBE','AMAT','AMGN','BAC','COIN','CRM','CRWD',
    'CSCO','DASH','DDOG','DHR','F','GOOGL','GS','HOOD','IBM','JNJ',
    'JPM','LLY','MA','MDB','MRK','MS','MSTR','MU','NET','OKTA',
    'ORCL','PANW','PFE','PYPL','RBLX','SNOW','SQ','TMO','TSLL','TXN',
    'UBER','UNH','V','WFC','ZS'
]

def download_one(sym: str) -> tuple[str, bool, str]:
    """Download 5y daily data for symbol, save as parquet."""
    path = STORAGE / f"{sym}_1d.parquet"
    if path.exists():
        return sym, True, "already exists"
    
    # Try yfinance download
    period = "5y"
    interval = "1d"
    
    try:
        df = yf.download(sym, period=period, interval=interval, auto_adjust=True, timeout=15)
        if df is None or df.empty:
            return sym, False, "no data"
        
        # Flatten columns if MultiIndex
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        
        df = df.reset_index()
        if 'Date' not in df.columns and 'Datetime' in df.columns:
            df = df.rename(columns={'Datetime': 'Date'})
        
        # Ensure required columns
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if col not in df.columns:
                return sym, False, f"missing {col}"
        
        # Add dividends and splits if not present
        for col in ['Dividends', 'Stock Splits']:
            if col not in df.columns:
                df[col] = 0.0
        
        df = df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'Dividends', 'Stock Splits']]
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.sort_values('Date').reset_index(drop=True)
        
        # Save
        df.to_parquet(path, index=True)
        return sym, True, f"saved {len(df)} rows"
    
    except Exception as e:
        return sym, False, str(e)[:50]

def main():
    all_missing = MISSING_HK + MISSING_US
    print(f"Downloading {len(all_missing)} missing symbols...")
    
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(download_one, sym): sym for sym in all_missing}
        for i, f in enumerate(as_completed(futures)):
            sym, ok, msg = f.result()
            results.append((sym, ok, msg))
            status = "✅" if ok else "❌"
            print(f"[{i+1}/{len(all_missing)}] {status} {sym}: {msg}")
    
    ok_count = sum(1 for _, ok, _ in results if ok)
    print(f"\nDone: {ok_count}/{len(results)} downloaded")
    
    # List what we have now
    existing = list(STORAGE.glob("*_1d.parquet"))
    print(f"Total parquet files: {len(existing)}")

if __name__ == "__main__":
    main()
