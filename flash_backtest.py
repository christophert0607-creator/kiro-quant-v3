import os
import sys
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

# 設置路徑
BASE_DIR = "/home/tsukii0607/.openclaw/workspace/skills/futu-api"
sys.path.append(BASE_DIR)

# 小祈的快閃回測魔法！
def flash_backtest(symbol="TSLA"):
    print(f"🚀 小祈正在抓取 {symbol} 的過去一週實戰數據...")
    
    # 1. 抓取過去 7 天的 15 分鐘 K 線數據
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)
    
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start_date, end=end_date, interval="15m")
    
    if df.empty:
        print("❌ 數據抓取失敗，小祈沒辦法變魔術了...")
        return

    print(f"📊 成功抓取 {len(df)} 根 K 線！正在執行 Kiro-V3 初步策略模擬...")

    # 2. 模擬一個簡單的 V3 邏輯：RSI(14) + EMA 交叉
    # (這只是預演，之後會用真正的 KiroLSTM 喔！)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    df['EMA_short'] = df['Close'].ewm(span=12, adjust=False).mean()
    df['EMA_long'] = df['Close'].ewm(span=26, adjust=False).mean()

    # 3. 繪製圖表
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1]})
    
    # 價格與 EMA
    ax1.plot(df.index, df['Close'], label='TSLA Price', color='white', alpha=0.9, linewidth=2)
    ax1.plot(df.index, df['EMA_short'], label='EMA 12', color='cyan', alpha=0.5)
    ax1.plot(df.index, df['EMA_long'], label='EMA 26', color='magenta', alpha=0.5)
    ax1.set_title(f'Kiro-V3 Flash Backtest: {symbol}', color='white', fontsize=16)
    ax1.set_facecolor('#0d1117')
    ax1.legend()
    ax1.tick_params(colors='white')

    # RSI
    ax2.plot(df.index, df['RSI'], color='gold', linewidth=1.5)
    ax2.axhline(70, color='red', linestyle='--', alpha=0.5)
    ax2.axhline(30, color='green', linestyle='--', alpha=0.5)
    ax2.set_facecolor('#0d1117')
    ax2.set_ylim(0, 100)
    ax2.set_ylabel('RSI', color='white')
    ax2.tick_params(colors='white')

    fig.patch.set_facecolor('#0d1117')
    plt.tight_layout()
    
    # 存檔
    output_path = "/home/tsukii0607/.openclaw/workspace/skills/futu-api/v3_pipeline/logs/flash_report.png"
    plt.savefig(output_path)
    print(f"✅ 快閃戰報繪製完成！已存放在: {output_path}")
    return output_path

if __name__ == "__main__":
    flash_backtest("TSLA")
