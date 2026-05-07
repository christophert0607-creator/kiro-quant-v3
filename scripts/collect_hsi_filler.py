import yfinance as yf
import pandas as pd
from pathlib import Path
import datetime
import time

# 補齊名單：選擇恆生科技指數及其他高流動性大盤股
FILLER_TICKERS = [
    "9888.HK", "9989.HK", "9999.HK", "9618.HK", "9633.HK", "9688.HK", "9868.HK", "9866.HK",
    "9961.HK", "9922.HK", "99S.HK", "3690.HK", "9988.HK", "0700.HK", "1024.HK", 
    "2318.HK", "2331.HK", "0941.HK", "1299.HK", "0388.HK",
    "2015.HK", "1810.HK", "2688.HK", "3988.HK", "0939.HK",
    "0001.HK", "0002.HK", "0003.HK", "0005.HK", "0006.HK",
    "0011.HK", "0012.HK", "0016.HK", "0017.HK", "0026.HK",
    "0063.HK", "0066.HK", "0101.HK", "0135.HK", "0267.HK",
    "0285.HK", "0340.HK", "0358.HK", "0380.HK", "0428.HK",
    "0489.HK", "0590.HK", "0669.HK", "0728.HK", "0762.HK",
    "0823.HK", "0981.HK", "1038.HK", "1088.HK", "1093.HK",
    "1113.HK", "1210.HK", "1378.HK", "1797.HK", "1836.HK",
    "1880.HK", "1918.HK", "2015.HK", "2333.HK", "2382.HK",
    "2388.HK", "2601.HK", "2628.HK", "2673.HK", "2701.HK",
    "2899.HK", "2914.HK", "2988.HK", "2989.HK", "3335.HK",
    "3968.HK", "3988.HK", "6098.HK", "6618.HK", "6699.HK"
]

# 移除重複
FILLER_TICKERS = list(set(FILLER_TICKERS))

def collect_filler():
    save_dir = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/data/hsi_training")
    save_dir.mkdir(parents=True, exist_ok=True)
    
    end_date = datetime.date.today().strftime('%Y-%m-%d')
    start_date = (datetime.date.today() - datetime.timedelta(days=5*365)).strftime('%Y-%m-%d')
    
    print(f"🚀 Starting Filler Collection to reach 100 stocks")
    
    success_count = 0
    
    for symbol in FILLER_TICKERS:
        try:
            file_path = save_dir / f"{symbol.replace('.', '_')}.parquet"
            if file_path.exists():
                continue # 已經有的就不重複抓
                
            print(f"Fetching {symbol}... ", end="")
            df = yf.download(symbol, start=start_date, end=end_date, interval="1d", progress=False)
            
            if df.empty or len(df) < 250:
                print("❌ Insufficient data")
                continue
            
            df.to_parquet(file_path)
            print("✅ Saved")
            success_count += 1
            time.sleep(0.2)
            
        except Exception as e:
            print(f"❌ Error: {e}")
            
    print(f"\n\n🎉 Filler Collection Finished!")
    print(f"New stocks added: {success_count}")

if __name__ == '__main__':
    collect_filler()
