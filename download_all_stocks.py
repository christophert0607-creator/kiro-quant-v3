#!/usr/bin/env python3
"""下載所有自選股的歷史數據"""

import yfinance as yf
import pandas as pd
from pathlib import Path
import json

# 讀取股票列表
with open('config.json') as f:
    stocks = json.load(f)['v3_live']['symbols_list']

print(f"開始下載 {len(stocks)} 隻股票的歷史數據...")

stocks_dir = Path('stocks')
stocks_dir.mkdir(exist_ok=True)

for symbol in stocks:
    try:
        print(f"下載 {symbol}...", end=" ")
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="2y", interval="1d")
        
        if len(df) > 0:
            df.to_csv(stocks_dir / f"{symbol}.csv")
            print(f"✅ {len(df)} rows")
        else:
            print(f"❌ 無數據")
    except Exception as e:
        print(f"❌ 錯誤: {e}")

print(f"\n完成！數據保存在 {stocks_dir}/")
