import yfinance as yf
import pandas as pd
from pathlib import Path
import datetime
import time

# 超級候選池：涵蓋大部分高流動性港股
SUPER_POOL = [
    "0700.HK", "9988.HK", "3690.HK", "1299.HK", "0941.HK", "1024.HK", "0388.HK", "0960.HK", "2318.HK", "2331.HK",
    "0939.HK", "0005.HK", "0002.HK", "0011.HK", "0175.HK", "0386.HK", "0883.HK", "0857.HK", "1109.HK", "1810.HK",
    "2688.HK", "2269.HK", "1211.HK", "2018.HK", "0688.HK", "2313.HK", "1398.HK", "3988.HK", "1928.HK", "2319.HK",
    "2020.HK", "0992.HK", "0001.HK", "0003.HK", "0006.HK", "0012.HK", "0017.HK", "0027.HK", "0066.HK", "0101.HK",
    "0135.HK", "0267.HK", "0285.HK", "0340.HK", "0358.HK", "0380.HK", "0428.HK", "0489.HK", "0590.HK", "0669.HK",
    "0728.HK", "0762.HK", "0823.HK", "0981.HK", "1038.HK", "1088.HK", "1093.HK", "1095.HK", "1113.HK", "1210.HK",
    "1378.HK", "1797.HK", "1836.HK", "1880.HK", "1918.HK", "2015.HK", "2333.HK", "2382.HK", "2388.HK", "2601.HK",
    "2628.HK", "2673.HK", "2701.HK", "2899.HK", "2914.HK", "2988.HK", "2989.HK", "3335.HK", "3968.HK", "3988.HK",
    "6098.HK", "6618.HK", "6699.HK", "9618.HK", "9633.HK", "9888.HK", "9989.HK", "9999.HK", "0008.HK", "0016.HK",
    "0026.HK", "0063.HK", "0088.HK", "0083.HK", "0344.HK", "0354.HK", "0407.HK", "0459.HK", "0489.HK", "0004.HK",
    "0009.HK", "0014.HK", "0018.HK", "0020.HK", "0021.HK", "0023.HK", "0025.HK", "0029.HK", "0032.HK", "0035.HK",
    "0038.HK", "0065.HK", "0070.HK", "0081.HK", "0100.HK", "0112.HK", "0114.HK", "0128.HK", "0132.HK", "0151.HK",
    "0168.HK", "0182.HK", "0190.HK", "0200.HK", "0202.HK", "0226.HK", "0238.HK", "0268.HK", "0288.HK", "0291.HK",
    "0300.HK", "0322.HK", "0334.HK", "03S.HK", "0369.HK", "0388.HK", "0400.HK", "0430.HK", "0450.HK", "0467.HK",
    "0489.HK", "0502.HK", "0535.HK", "0547.HK", "0582.HK", "0593.HK", "0602.HK", "0636.HK", "0669.HK", "0688.HK",
    "0700.HK", "0728.HK", "0762.HK", "0803.HK", "0823.HK", "0838.HK", "0857.HK", "0881.HK", "0883.HK", "0939.HK",
    "0941.HK", "0960.HK", "0968.HK", "0981.HK", "0992.HK", "1013.HK", "1024.HK", "1035.HK", "1038.HK", "1044.HK",
    "1088.HK", "1093.HK", "1095.HK", "1109.HK", "1113.HK", "1211.HK", "1299.HK", "1378.HK", "1398.HK", "1797.HK",
    "1810.HK", "1836.HK", "1880.HK", "1918.HK", "1928.HK", "2015.HK", "2020.HK", "2313.HK", "2318.HK", "2319.HK",
    "2331.HK", "2333.HK", "2382.HK", "2388.HK", "2601.HK", "2628.HK", "2673.HK", "2688.HK", "2701.HK", "2899.HK",
    "2914.HK", "2988.HK", "2989.HK", "3335.HK", "3690.HK", "3968.HK", "3988.HK", "6098.HK", "6618.HK", "6699.HK",
    "9618.HK", "9633.HK", "9866.HK", "9868.HK", "9888.HK", "9988.HK", "9989.HK", "9999.HK"
]

# 移除重複
SUPER_POOL = list(set(SUPER_POOL))

def collect_to_100():
    save_dir = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/data/hsi_training")
    save_dir.mkdir(parents=True, exist_ok=True)
    
    # 獲取當前文件數量
    current_files = list(save_dir.glob("*.parquet"))
    num_existing = len(current_files)
    print(f"📊 Current stock count: {num_existing}")
    
    if num_existing >= 100:
        print("✅ Target reached! No more collection needed.")
        return

    target_count = 100
    needed = target_count - num_existing
    print(f"🎯 Target: {target_count} | Needed: {needed}")
    
    end_date = datetime.date.today().strftime('%Y-%m-%d')
    start_date = (datetime.date.today() - datetime.timedelta(days=5*365)).strftime('%Y-%m-%d')
    
    success_count = 0
    
    for symbol in SUPER_POOL:
        # 檢查是否已經存在
        file_path = save_dir / f"{symbol.replace('.', '_')}.parquet"
        if file_path.exists():
            continue
            
        try:
            print(f"Attempting {symbol}... ", end="")
            df = yf.download(symbol, start=start_date, end=end_date, interval="1d", progress=False)
            
            if df.empty or len(df) < 250:
                print("❌ Failed")
                continue
            
            df.to_parquet(file_path)
            print("✅ Saved")
            success_count += 1
            
            # 檢查是否達到 100 隻
            if (num_existing + success_count) >= 100:
                print(f"\n🎉 Target 100 reached!")
                break
                
        except Exception as e:
            print(f"❌ Error: {e}")
            
        time.sleep(0.2)
        
    print(f"\n\n🎉 Final Result: {num_existing + success_count} stocks collected.")

if __name__ == '__main__':
    collect_to_100()
