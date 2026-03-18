#!/usr/bin/env python3
import time
import logging
import argparse
import yfinance as yf
import pandas as pd
from typing import List, Dict
from datetime import datetime, timedelta
from db_manager import DatabaseManager
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("Collector")

class DataCollector:
    """Automated data collector with Batching and Smart Repair logic."""

    def __init__(self, db_path: str = "kiro_quant.db"):
        self.db = DatabaseManager(db_path)
        self.generator = TechnicalIndicatorGenerator()
        self.batch_size = 10
        self.request_delay = 5  # seconds between batches

    def _process_and_save(self, df: pd.DataFrame, symbol: str):
        """Helper to generate indicators and save to SQL."""
        if df.empty:
            return
        try:
            # Generate technical indicators
            featured_df = self.generator.generate(df)
            # Save to SQL
            self.db.save_data(featured_df, symbol=symbol)
            logger.info(f"Successfully saved {len(featured_df)} rows for {symbol}")
        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")

    def collect_batch(self, symbols: List[str], period: str = "2y", interval: str = "1d"):
        """Fetch data in batches of N symbols to optimize performance while staying safe."""
        for i in range(0, len(symbols), self.batch_size):
            chunk = symbols[i : i + self.batch_size]
            logger.info(f"Fetching batch [{i//self.batch_size + 1}]: {chunk}")
            
            # Exponential Backoff Retry Logic
            max_retries = 5
            attempt = 0
            success = False
            data = None
            
            while attempt < max_retries and not success:
                try:
                    data = yf.download(chunk, period=period, interval=interval, group_by='ticker', progress=False)
                    if data.empty:
                         raise ValueError("Received empty dataset from yfinance")
                    success = True
                except Exception as e:
                    attempt += 1
                    wait_time = (2 ** attempt) + 5 # 7s, 9s, 13s, 21s, 37s
                    logger.warning(f"Batch download attempt {attempt} failed: {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)

            if success and data is not None:
                try:
                    for symbol in chunk:
                        if len(chunk) > 1:
                            symbol_df = data[symbol].dropna(how='all').reset_index()
                        else:
                            symbol_df = data.dropna(how='all').reset_index()
                        
                        self._process_and_save(symbol_df, symbol)
                    
                    time.sleep(self.request_delay)
                except Exception as e:
                    logger.error(f"Error processing batch data for {chunk}: {e}")
            else:
                logger.error(f"Batch {chunk} failed after {max_retries} attempts.")

    def smart_repair(self, symbols: List[str], target_period_days: int = 730):
        """Identify missing symbols or data gaps and repair them."""
        logger.info("Starting Database Health Check & Repair...")
        health = self.db.check_health(symbols)
        
        to_fetch_new = []
        to_repair_gap = []
        
        for sym, stats in health.items():
            if not stats["start"]:
                logger.info(f"Target [{sym}]: No data found. Adding to full fetch.")
                to_fetch_new.append(sym)
                continue
            
            # Simple check: if less than X days of data, it might have a gap or be incomplete
            # (Note: This is a heuristic. A more complex check would look for interior date gaps)
            last_date = datetime.strptime(stats["end"].split(".")[0], "%Y-%m-%d %H:%M:%S")
            days_old = (datetime.now() - last_date).days
            
            if days_old > 2:
                logger.info(f"Target [{sym}]: Data is {days_old} days old. Needs catch-up.")
                to_repair_gap.append(sym)
            elif stats["count"] < (target_period_days * 0.6): # roughly 252 trading days per year
                 logger.info(f"Target [{sym}]: Data count {stats['count']} looks low. Refreshing.")
                 to_repair_gap.append(sym)

        # Execute repairs
        if to_fetch_new:
            logger.info(f"Repairing {len(to_fetch_new)} new symbols...")
            self.collect_batch(to_fetch_new, period=f"{target_period_days}d")
            
        if to_repair_gap:
            logger.info(f"Refilling {len(to_repair_gap)} existing symbols...")
            # For repairs, we fetch the last year to fill gaps
            self.collect_batch(to_repair_gap, period="1y")

        logger.info("Smart Repair complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kiro Quant Smart Collector & Repairer")
    parser.add_argument("--symbols", default="TSLA,AAPL,NVDA,META,00700.HK,09988.HK", help="Comma separated symbols")
    parser.add_argument("--mode", choices=["batch", "repair", "live"], default="batch")
    parser.add_argument("--period", default="2y", help="Historical period")
    parser.add_argument("--interval", default="1d", help="Interval")
    
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",")]
    
    collector = DataCollector()
    
    if args.mode == "batch":
        collector.collect_batch(symbols, period=args.period, interval=args.interval)
    elif args.mode == "repair":
        collector.smart_repair(symbols)
    elif args.mode == "live":
        # Live mode: just catch up on last 5 days every X minutes
        while True:
            collector.collect_batch(symbols, period="5d", interval=args.interval)
            logger.info("Cycle complete. Sleeping for 1 hour...")
            time.sleep(3600)
