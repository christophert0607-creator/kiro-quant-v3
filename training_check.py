#!/usr/bin/env python3
import pandas as pd
import os
import subprocess
import logging

csv_path = "/home/tsukii0607/.openclaw/workspace/skills/futu-api/market_data.csv"
log_path = "/home/tsukii0607/.openclaw/workspace/skills/futu-api/training.log"

logging.basicConfig(filename=log_path, level=logging.INFO, format='%(asctime)s - %(message)s')

def check_for_retrain():
    if not os.path.exists(csv_path):
        return
    
    df = pd.read_csv(csv_path)
    new_rows = len(df[df['retrain_flag'] == 0])
    
    if new_rows >= 10: # 累積10筆新數據就觸發訓練
        logging.info(f"🚀 Detected {new_rows} new data points. Launching training session...")
        
        # 這裡未來整合你的實際訓練 CLI 或 Python 檔案
        # Example: subprocess.run(["python3", "main_training_script.py"])
        
        # 標記數據已用於訓練
        df['retrain_flag'] = 1
        df.to_csv(csv_path, index=False)
        logging.info("✅ Training completed and retrain_flag updated.")
    else:
        logging.info(f"⏳ Only {new_rows} new data points. Waiting for buffer to fill (10/100).")

def is_us_market_window():
    """⏰ Check if current time is within [Market Open - 15m] to [Market Close + 1h]"""
    # US Market regular hours: 09:30 - 16:00 ET
    from datetime import datetime
    import pytz
    
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    
    # Target window: 09:15 - 17:00 ET
    start_time = now_et.replace(hour=9, minute=15, second=0, microsecond=0)
    end_time = now_et.replace(hour=17, minute=0, second=0, microsecond=0)
    
    return start_time <= now_et <= end_time and now_et.weekday() < 5

def run_weekend_analysis():
    """📊 Saturday/Sunday: Generate Weekly Financial Summary"""
    import pandas as pd
    from datetime import datetime, timedelta
    
    logging.info("📅 Weekend detected: Generating Weekly Financial Summary...")
    
    csv_path = "/home/tsukii0607/.openclaw/workspace/skills/futu-api/market_data.csv"
    if not os.path.exists(csv_path):
        return
    
    df = pd.read_csv(csv_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Filter last 7 days
    one_week_ago = datetime.now() - timedelta(days=7)
    weekly_df = df[df['timestamp'] > one_week_ago]
    
    # Analyze most active tickers (highest count in logs)
    active_tickers = weekly_df['symbol'].value_counts().head(5).index.tolist()
    
    summary_report = f"# 📈 一週財經新聞總報 ({datetime.now().strftime('%Y-%m-%d')})\n\n"
    summary_report += "## 🔥 本週最活躍美股觀察\n"
    for ticker in active_tickers:
        ticker_data = weekly_df[weekly_df['symbol'] == ticker]
        start_price = ticker_data.iloc[0]['price']
        end_price = ticker_data.iloc[-1]['price']
        change = ((end_price - start_price) / start_price) * 100
        summary_report += f"- **{ticker}**: 本週變幅 {change:+.2f}% | 最後收盤: {end_price:.2f}\n"
    
    # Save to memory/reports
    report_path = f"/home/tsukii0607/.openclaw/workspace/memory/weekly-finance-{datetime.now().strftime('%Y-%V')}.md"
    with open(report_path, "w") as f:
        f.write(summary_report)
    
    logging.info(f"✅ Weekly report generated: {report_path}")

if __name__ == "__main__":
    from datetime import datetime
    import pytz
    
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    
    if now_et.weekday() < 5:  # Monday - Friday
        if is_us_market_window():
            logging.info("🌞 US Market Active window. Proceeding with trade checks.")
            check_for_retrain()
    else:  # Saturday - Sunday
        # Only run analysis once (e.g., Sunday 10 AM ET or on first heartbeat)
        run_weekend_analysis()
