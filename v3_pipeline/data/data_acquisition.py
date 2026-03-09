import os
import sys
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import logging

class DataAcquisition:
    def __init__(self, data_path="./data_storage/"):
        self.data_path = data_path
        if not os.path.exists(data_path):
            os.makedirs(data_path)

    def fetch_history(self, symbol, start_date, end_date):
        """下載歷史數據 (yfinance)"""
        try:
            print(f"📡 正在從 yfinance 抓取 {symbol} 的歷史數據...")
            data = yf.download(symbol, start=start_date, end=end_date)
            file_name = f"{symbol}_history.csv"
            data.to_csv(os.path.join(self.data_path, file_name))
            print(f"✅ {symbol} 歷史數據下載完成！存儲至 {file_name}")
            return data
        except Exception as e:
            print(f"❌ 抓取失敗: {e}")
            return None

    def fetch_realtime(self, symbol, source="FUTU"):
        """實時數據抓取 (Futu/Massive)"""
        # 這裡會連動到我們現有的 futu_api.py 的邏輯
        pass

if __name__ == "__main__":
    da = DataAcquisition()
    da.fetch_history("TSLA", "2020-01-01", datetime.now().strftime("%Y-%m-%d"))
