import sys
import os
import pandas as pd
import torch
import matplotlib.pyplot as plt
from datetime import datetime

# 設置路徑
BASE_DIR = "/home/tsukii0607/.openclaw/workspace/skills/futu-api"
V3_PIPELINE = os.path.join(BASE_DIR, "v3_pipeline")
sys.path.append(BASE_DIR)

from v3_pipeline.models.manager import ModelManager, DataPreparer
from v3_pipeline.models.brain import KiroLSTM
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator
import yfinance as yf

def deep_backtest_v3(symbol="TSLA"):
    print(f"🚀 小祈正在執行 {symbol} 的 V3.5 旗艦級深度回測...")
    
    # 1. 抓取數據 (過去 5 天 分鐘線)
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="5d", interval="1m")
    if df.empty:
        print("❌ 沒抓到數據，任務失敗...")
        return
    
    # 🚀 Kiro Fix: yfinance 回傳的是 Index Date, 需要轉為 Column
    df = df.reset_index()
    dt_col = "Datetime" if "Datetime" in df.columns else "Date"
    df = df.rename(columns={dt_col: "Date"})

    # 2. 生成 20 個 V3 指標
    gen = TechnicalIndicatorGenerator()
    featured_df = gen.generate(df)
    
    # 3. 初始化旗艦大腦
    lookback = 60
    # 偵測指標數量
    input_dim = len(featured_df.columns) - 7 # 扣除原始 OHLCV + Date
    model = KiroLSTM(input_dim=input_dim)
    preparer = DataPreparer(lookback=lookback)
    manager = ModelManager(model=model, data_preparer=preparer)
    
    # 擬合
    print(f"🧠 大腦正在擬合 {len(featured_df)} 根數據軌跡...")
    try:
        manager.data_preparer.fit_transform(featured_df)
    except Exception as e:
        print(f"❌ 擬合失敗: {e}")
        return

    # 4. 模擬回測
    # 我們每 10 根數據做一次預測，模擬開盤後的決策
    results = []
    prices = featured_df['Close'].tolist()
    timestamps = featured_df.index.tolist()
    
    print("📈 正在進行跨時空預測模擬...")
    for i in range(lookback, len(featured_df), 10):
        window = featured_df.iloc[i-lookback:i]
        pred = manager.predict(window)
        curr_price = prices[i]
        results.append({'time': timestamps[i], 'price': curr_price, 'pred': pred})
    
    res_df = pd.DataFrame(results)
    
    # 5. 繪製精美戰報圖
    plt.figure(figsize=(15, 8))
    plt.style.use('dark_background')
    
    plt.plot(res_df['time'], res_df['price'], label='Actual Price', color='#58a6ff', alpha=0.8)
    plt.scatter(res_df['time'], res_df['pred'], c=res_df['pred'], cmap='RdYlGn', label='Kiro-LSTM Predict', s=10)
    
    plt.title(f"🌌 Kiro-Quant V3.5 Deep Backtest: {symbol} (5-Day 1m Flow)", fontsize=16)
    plt.xlabel("Timeline")
    plt.ylabel("Price (USD)")
    plt.legend()
    plt.grid(color='#30363d', linestyle='--')
    
    report_path = "/home/tsukii0607/.openclaw/workspace/skills/futu-api/v3_pipeline/logs/deep_backtest_report.png"
    plt.savefig(report_path)
    print(f"✅ 旗艦級戰報繪製完成！已存放在: {report_path}")
    return report_path

if __name__ == "__main__":
    deep_backtest_v3("TSLA")
