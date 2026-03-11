import os
import sys
import pandas as pd
import yfinance as yf
import time
import logging
from concurrent.futures import ThreadPoolExecutor

# 設置基礎路徑
BASE_DIR = "/home/tsukii0607/.openclaw/workspace/skills/futu-api"
STORAGE_DIR = os.path.join(BASE_DIR, "v3_pipeline/data/storage/base_10y")
os.makedirs(STORAGE_DIR, exist_ok=True)

# 設定日誌
logging.basicConfig(level=logging.INFO, format='💖 [Kiro-BigData-10Y] %(asctime)s - %(message)s')
logger = logging.getLogger("BigDataExplorer")

def download_10y_base(symbol):
    try:
        logger.info(f"🏗️ 正在挖掘 {symbol} 的「十載春秋」地基...")
        ticker = yf.Ticker(symbol)
        # 抓取 10 年日線數據
        df = ticker.history(period="10y", interval="1d")
        
        if df.empty:
            logger.warning(f"⚠️ {symbol} 返回數據為空，跳過。")
            return
        
        # 格式標準化
        df = df.reset_index()
        target_path = os.path.join(STORAGE_DIR, f"{symbol}_10y_base.parquet")
        df.to_parquet(target_path, compression="snappy")
        logger.info(f"✅ {symbol} 十年地基入庫成功！(共 {len(df)} 根 K 線)")
    except Exception as e:
        logger.error(f"❌ {symbol} 深度挖掘失敗: {e}")

def run_scout_mission_10y():
    # 核心 111 艦隊名單 (保持與 5y 一致)
    core_elite = ["TSLA", "NVDA", "F", "GOOG", "AAPL", "MSFT", "AMZN", "SOXL", "TSLL", "TQQQ", "SQQQ"]
    tech = ["AMD", "AVGO", "ORCL", "CRM", "INTC", "CSCO", "ADBE", "TXN", "QCOM", "IBM"]
    consumer = ["HD", "NKE", "MCD", "LOW", "SBUX", "BKNG", "TJX", "ABNB"]
    fin = ["JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "PYPL", "COIN", "HOOD"]
    comm = ["META", "NFLX", "DIS", "GOOGL", "CHTR", "TMUS", "VZ", "T", "SNAP", "PINS"]
    health = ["LLY", "UNH", "JNJ", "ABBV", "MRK", "PFE", "TMO", "ISRG", "AMGN", "MRNA"]
    indus = ["GE", "CAT", "HON", "LMT", "BA", "UPS", "FDX", "UBER", "AAL", "DAL"]
    energy = ["XOM", "CVX", "COP", "SLB", "EOG", "VLO", "MPC", "OXY", "HAL", "CHPT"]
    semi_plus = ["ASML", "MU", "LRCX", "ADI", "KLAC", "SNPS", "CDNS", "MSTR", "SMCI", "ARM"]
    materials = ["LIN", "APD", "FCX", "SHW", "NEM", "CTVA", "ALB", "MP", "CF", "MOS"]
    utils_reit = ["NEE", "DUK", "SO", "PLD", "AMT", "CCI", "EQIX", "DLR", "OKTA", "NET"]
    
    super_fleet = list(set(core_elite + tech + consumer + fin + comm + health + indus + energy + semi_plus + materials + utils_reit))
    
    logger.info(f"🚀 小祈啟動「十載春秋」超深度挖掘！目標總數: {len(super_fleet)}")
    
    # 排除已下載好的
    existing = [f.split('_')[0] for f in os.listdir(STORAGE_DIR) if f.endswith('.parquet')]
    remaining = [s for s in super_fleet if s not in existing]
    
    logger.info(f"📊 已存儲: {len(existing)}, 剩餘需下鑽: {len(remaining)}")

    if not remaining:
        logger.info("✨ 全員「十載地基」已完工，指揮官可以檢閱啦！")
        return

    # 分批次下載 (每批 10 隻)
    batch_size = 10
    for i in range(0, len(remaining), batch_size):
        batch = remaining[i : i + batch_size]
        logger.info(f"📡 正在下鑽第 {i//batch_size + 1} 重深層: {batch}")
        with ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(download_10y_base, batch)
        time.sleep(8) # 深度挖掘需要更長的冷卻時間喔！
    
    logger.info("✨ 111 奇蹟艦隊「十載春秋」全域入庫完成！🏆")

if __name__ == "__main__":
    run_scout_mission_10y()
