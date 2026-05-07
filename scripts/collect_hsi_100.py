import yfinance as yf
import pandas as pd
from pathlib import Path
import datetime
import time

# 1. 定義恆指前 100 隻核心股票清單 (精確且不重複)
# 包含金融、科技、電信、房產等核心權重股
HSI_100_TICKERS = [
    "0700.HK", "9988.HK", "3690.HK", "1299.HK", "0941.HK", "1024.HK", "0388.HK", "0960.HK", "2318.HK", "2331.HK",
    "0939.HK", "0005.HK", "0002.HK", "0011.HK", "0175.HK", "0386.HK", "0883.HK", "0857.HK", "1109.HK", "1810.HK",
    "2688.HK", "2269.HK", "1211.HK", "2018.HK", "0688.HK", "2313.HK", "1398.HK", "3988.HK", "1928.HK", "2319.HK",
    "2020.HK", "0992.HK", "0001.HK", "0003.HK", "0006.HK", "0012.HK", "0017.HK", "0027.HK", "0066.HK", "0101.HK",
    "0135.HK", "0267.HK", "0285.HK", "0340.HK", "0358.HK", "0380.HK", "0428.HK", "0489.HK", "0590.HK", "0669.HK",
    "0728.HK", "0762.HK", "0823.HK", "0981.HK", "1038.HK", "1088.HK", "1093.HK", "1095.HK", "1113.HK", "1210.HK",
    "1378.HK", "1797.HK", "1836.HK", "1880.HK", "1918.HK", "2015.HK", "2020.HK", "2333.HK", "2382.HK", "2388.HK",
    "2601.HK", "2628.HK", "2673.HK", "2701.HK", "2899.HK", "2914.HK", "2988.HK", "2989.HK", "3335.HK", "3968.HK",
    "3988.HK", "6098.HK", "6618.HK", "6699.HK", "9618.HK", "9633.HK", "9888.HK", "9989.HK", "9999.HK", "0008.HK",
    "0016.HK", "0026.HK", "0063.HK", "0088.HK", "0083.HK", "0344.HK", "0354.HK", "0407.HK", "0459.HK", "0489.HK"
]

# 移除重複
HSI_100_TICKERS = list(set(HSI_100_TICKERS))

def collect_data():
    save_dir = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/data/hsi_training")
    save_dir.mkdir(parents=True, exist_ok=True)
    
    end_date = datetime.date.today().strftime('%Y-%m-%d')
    start_date = (datetime.date.today() - datetime.timedelta(days=5*365)).strftime('%Y-%m-%d')
    
    print(f"🚀 Starting HSI-100 Data Collection ({start_date} to {end_date})")
    print(f"Target: {len(HSI_100_TICKERS)} symbols")
    
    success_count = 0
    fail_count = 0
    
    for symbol in HSI_100_TICKERS:
        try:
            print(f"Fetching {symbol}... ", end="")
            # Fetch daily data
            df = yf.download(symbol, start=start_date, end=end_date, interval="1d", progress=False)
            
            if df.empty or len(df) < 250: # 至少要有1年的數據才有用
                print("❌ Insufficient data")
                fail_count += 1
                continue
            
            # 儲存為 Parquet
            file_path = save_dir / f"{symbol.replace('.', '_')}.parquet"
            df.to_parquet(file_path)
            print("✅ Saved")
            success_count += 1
            
            # 避免請求過快
            time.sleep(0.2)
            
        except Exception as e:
            print(f"❌ Error: {e}")
            fail_count += 1
            
    print(f"\n\n🎉 Collection Finished!")
    print(f"Success: {success_count} | Failed: {fail_count}")
    print(f"Data saved to: {save_dir}")

if __name__ == '__main__':
    collect_data()
