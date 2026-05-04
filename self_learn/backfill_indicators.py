#!/usr/bin/env python3
"""
Backfill technical indicators for historical predictions.

For each prediction that has NULL feature_vector, fetch Yahoo Finance OHLCV data
at signal time, compute RSI/MACD/BB, and store as feature_vector.

This repairs the data collection gap so build_training_data() can use
real market-regime features instead of falling back to _basic_features().
"""

import sys, os, pickle, sqlite3, json, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

# Suppress TA library warnings during backfill
logging.getLogger("TechnicalIndicatorGenerator").setLevel(logging.ERROR)

# Add workspace root to path so 'self_learn' and 'v3_pipeline' are importable
WORKSPACE = "/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3"
sys.path.insert(0, WORKSPACE)
os.chdir(WORKSPACE)

from self_learn.models import session_scope, Prediction, Signal, Outcome, get_stats
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator

import pandas as pd
import numpy as np
import yfinance as yf


def fetch_yf_at(symbol: str, timestamp: datetime, lookback: int = 60) -> Optional[pd.DataFrame]:
    """Fetch Yahoo Finance daily data ending at or before the given timestamp."""
    try:
        end = timestamp + timedelta(days=3)
        # Calendar days needed to guarantee ~lookback trading days (~6.5 trading days/week)
        cal_days = max(90, int(lookback * 1.6))
        start = end - timedelta(days=cal_days)
        df = yf.download(symbol, start=start.strftime("%Y-%m-%d"), 
                         end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
        if df.empty or len(df) < 30:  # need at least 30 trading days for RSI
            return None
        
        # Flatten multi-level columns from yfinance
        df = df.reset_index()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[0]) for c in df.columns]  # 'Date', 'Close', 'High', etc.
        
        # Standardize column names
        rename = {c: c.capitalize() for c in df.columns 
                  if c.lower() in {'open', 'high', 'low', 'close', 'volume', 'date'}}
        df = df.rename(columns=rename)
        
        required = {'Date', 'Open', 'High', 'Low', 'Close', 'Volume'}
        if not required.issubset(set(df.columns)):
            return None
        
        df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
        return df
    except Exception:
        return None


def compute_indicators(df: pd.DataFrame) -> dict:
    """Compute technical indicators from OHLCV dataframe."""
    tg = TechnicalIndicatorGenerator()
    try:
        result = tg.generate(df)
        row = result.iloc[-1]
        indicators = {}
        for k in ["RSI_14", "MACD_HIST", "BB_LOWER", "BB_MIDDLE", "BB_UPPER", 
                  "SMA_5", "SMA_20", "ATR_14", "CCI_14", "WILLR_14"]:
            if k in row.index:
                v = row.get(k)
                if v is not None and not (isinstance(v, float) and np.isnan(v)):
                    indicators[k] = float(v)
        # Add regime features
        close = row.get("Close")
        bb_lower = row.get("BB_LOWER")
        bb_upper = row.get("BB_UPPER")
        if close and bb_lower and bb_upper and bb_upper != bb_lower:
            indicators["BB_POSITION"] = (float(close) - float(bb_lower)) / (float(bb_upper) - float(bb_lower))
        return indicators
    except Exception:
        return {}


def backfill_predictions(batch_limit: int = 500, dry_run: bool = False) -> dict:
    """Backfill NULL feature_vectors for predictions linked to closed signals."""
    stats = {"checked": 0, "backfilled": 0, "failed": 0, "already_filled": 0, "skipped_no_signal": 0}
    
    with session_scope() as session:
        # Get predictions with NULL feature_vector that are linked to CLOSED signals
        rows = (
            session.query(Prediction, Signal)
            .join(Signal, Prediction.id == Signal.prediction_id)
            .filter(
                Prediction.feature_vector == None,
                Signal.status == "CLOSED"
            )
            .order_by(Signal.created_at.desc())
            .limit(batch_limit)
            .all()
        )
        
        for pred, signal in rows:
            stats["checked"] += 1
            if not signal.created_at:
                stats["skipped_no_signal"] += 1
                continue
                
            # Fetch historical data
            df = fetch_yf_at(pred.symbol, signal.created_at)
            if df is None:
                stats["failed"] += 1
                continue
                
            indicators = compute_indicators(df)
            if not indicators:
                stats["failed"] += 1
                continue
            
            if dry_run:
                print(f"  [DRY] {pred.symbol} at {signal.created_at} → {indicators}")
                stats["backfilled"] += 1
                continue
            
            # Serialize and store
            fv_bytes = pickle.dumps(indicators)
            pred.feature_vector = fv_bytes
            session.add(pred)
            stats["backfilled"] += 1
            
        session.commit()
    return stats


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"Starting backfill {'(DRY RUN)' if dry_run else ''}")
    print()
    
    # Show current state
    with session_scope() as s:
        total = s.query(Prediction).count()
        null_fv = s.query(Prediction).filter(Prediction.feature_vector == None).count()
        linked_null = (
            s.query(Prediction)
            .join(Signal, Prediction.id == Signal.prediction_id)
            .filter(Prediction.feature_vector == None, Signal.status == "CLOSED")
            .count()
        )
    print(f"Total predictions : {total}")
    print(f"NULL feature_vector: {null_fv}")
    print(f"NULL + CLOSED signal: {linked_null}")
    print()
    
    if dry_run:
        print("--- Dry run: showing what would be backfilled ---")
    
    result = backfill_predictions(batch_limit=500, dry_run=dry_run)
    
    print(f"\nResults:")
    print(f"  Checked   : {result['checked']}")
    print(f"  Backfilled: {result['backfilled']}")
    print(f"  Failed    : {result['failed']}")
    print(f"  Skipped   : {result['skipped_no_signal']}")
    
    if not dry_run:
        # Verify
        with session_scope() as s:
            filled = (
                s.query(Prediction)
                .join(Signal, Prediction.id == Signal.prediction_id)
                .filter(Prediction.feature_vector != None, Signal.status == "CLOSED")
                .count()
            )
            print(f"\nVerification — filled CLOSED predictions: {filled}")
        
        # Show sample
        print("\nSample backfilled feature_vectors:")
        with session_scope() as s:
            samples = (
                s.query(Prediction, Signal)
                .join(Signal, Prediction.id == Signal.prediction_id)
                .filter(Prediction.feature_vector != None, Signal.status == "CLOSED")
                .limit(3)
                .all()
            )
        for pred, sig in samples:
            d = pickle.loads(pred.feature_vector)
            print(f"  {sig.symbol} | {list(d.keys())} | RSI={d.get('RSI_14', 'N/A'):.1f}")


if __name__ == "__main__":
    main()
