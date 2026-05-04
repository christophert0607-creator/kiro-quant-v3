"""
Historical Backfill Script for Kiro Quant V3
==========================================
用真實歷史價格數據生成額外訓練樣本，擴充 self_learn 數據庫。

原理：
- 對過去 N 個月的歷史 K 線数据进行回測
- 每個週期根據技術指標模擬進場信號
- 根據實際未來價格變動計算 P&L（已知結果，非模擬）
- 將這些「已知結果」的信號注入 training log
"""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

WORKSPACE = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3")
DB_PATH = WORKSPACE / "self_learn" / "trading_bot.db"

# 目標股票池（高流動性美股 + 主要港股）
BACKFILL_SYMBOLS = [
    # Top US tech/liquid
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "AVGO",
    "CRM", "ADBE", "CSCO", "ACN", "IBM", "INTC", "QCOM", "TXN", "AMAT", "MU",
    "NFLX", "COIN", "MSTR", "UBER", "DASH", "SNOW", "PANW", "CRWD", "ZS", "NET",
    "DDOG", "OKTA", "MDB", "PLTR", "ARM", "HOOD", "RBLX", "F", "SMCI",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "PYPL", "COST",
    "LLY", "UNH", "JNJ", "PFE", "ABT", "TMO", "DHR", "ABBV", "MRK", "AMGN",
    # HK
    "0700.HK", "9988.HK", "3690.HK", "1024.HK", "2318.HK", "1299.HK",
    "0941.HK", "0388.HK", "9618.JD", "2800.HK",
]

# 參數
LOOKBACK_MONTHS = 12          # 回測 12 個月歷史
MIN_HOLD_HOURS = 4             # 最短持倉（小時）
MAX_HOLD_HOURS = 48            # 最長持倉（小時）
RSI_OVERSOLD = 35              # RSI 低於此值考慮進場
WIN_THRESHOLD_PCT = 0.01       # 上漲 >1% 視為獲利
LOSS_THRESHOLD_PCT = -0.01     # 下跌 >1% 視為虧損


def compute_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.inf)
    return 100 - (100 / (1 + rs))


def compute_macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line, macd - signal_line  # macd, signal, histogram


def fetch_history(symbol: str, months: int = 12) -> Optional[pd.DataFrame]:
    """下載歷史 K 線數據（1小時 K）。"""
    try:
        end = datetime.now()
        start = end - timedelta(days=months * 30)
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end, interval="1h", auto_adjust=True)
        if df.empty or len(df) < 100:
            return None
        df = df.rename(columns={"Open": "Open", "High": "High", "Low": "Low", "Close": "Close", "Volume": "Volume"})
        df.index = df.index.tz_localize(None) if df.index.tz else df.index
        return df
    except Exception as e:
        print(f"  [WARN] {symbol} fetch failed: {e}")
        return None


def generate_signals(df: pd.DataFrame, symbol: str) -> list[dict]:
    """對歷史數據生成模擬信號（僅用於擴充 training data）。"""
    signals = []
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    rsi = compute_rsi(close)
    macd, macd_signal, macd_hist = compute_macd(close)

    # SMA
    sma5 = close.rolling(5).mean()
    sma20 = close.rolling(20).mean()

    for i in range(50, len(df) - MAX_HOLD_HOURS):  # need future data
        row = df.iloc[i]
        price = row["Close"]

        # Entry: RSI oversold + MACD positive
        rsi_val = rsi.iloc[i]
        macd_h = macd_hist.iloc[i]
        trend_ok = price >= sma5.iloc[i] if not pd.isna(sma5.iloc[i]) else True

        if rsi_val < RSI_OVERSOLD and macd_h > 0 and trend_ok:
            entry_price = price
            entry_time = df.index[i]

            # Simulate holding for MIN_HOLD_HOURS to MAX_HOLD_HOURS
            for hold_h in [MIN_HOLD_HOURS, MAX_HOLD_HOURS]:
                future_idx = i + hold_h
                if future_idx >= len(df):
                    break
                exit_price = df.iloc[future_idx]["Close"]
                pnl_pct = (exit_price - entry_price) / entry_price
                pnl_dollar = pnl_pct * 1000  # rough $1000 notional

                # Determine label: 1=profitable, 0=loss
                is_profitable = 1 if pnl_pct > WIN_THRESHOLD_PCT else 0
                is_loss = 1 if pnl_pct < LOSS_THRESHOLD_PCT else 0

                # Compute feature vector (same as live system)
                # Confidence: scaled raw ratio
                raw_ratio = abs(exit_price - entry_price) / max(entry_price, 1e-9)
                confidence = min(1.0, raw_ratio * 50.0)

                signals.append({
                    "symbol": symbol,
                    "action": "BUY",
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "pnl": pnl_dollar,
                    "pnl_pct": pnl_pct * 100,  # store as percentage
                    "hold_minutes": hold_h * 60,
                    "confidence": confidence,
                    "rsi_entry": rsi_val,
                    "macd_hist": macd_h,
                    "label": is_profitable,
                    "entry_time": entry_time,
                    "exit_time": df.index[future_idx],
                })

                # Only use best outcome per entry
                if hold_h == MIN_HOLD_HOURS and is_profitable:
                    break  # take profit early

    return signals


def insert_backfill_data(signals: list[dict], conn: sqlite3.Connection) -> int:
    """將生成的信號插入數據庫。"""
    inserted = 0
    cursor = conn.cursor()

    for sig in signals:
        pred_id = str(uuid.uuid4())
        sig_id = str(uuid.uuid4())
        now_ts = datetime.now(timezone.utc).isoformat()

        # Insert prediction
        cursor.execute("""
            INSERT OR IGNORE INTO predictions
            (id, symbol, predicted_price, confidence, feature_vector, model_version_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            pred_id,
            sig["symbol"],
            float(sig["exit_price"]),
            float(sig["confidence"]),
            None,
            "backfill-v1",
            sig["entry_time"].isoformat() if hasattr(sig["entry_time"], 'isoformat') else str(sig["entry_time"]),
        ))

        # Insert signal
        cursor.execute("""
            INSERT OR IGNORE INTO signals
            (id, prediction_id, action, entry_price, size, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            sig_id,
            pred_id,
            sig["action"],
            float(sig["entry_price"]),
            100,  # nominal size
            "CLOSED",
            sig["entry_time"].isoformat() if hasattr(sig["entry_time"], 'isoformat') else str(sig["entry_time"]),
        ))

        # Insert outcome
        cursor.execute("""
            INSERT OR IGNORE INTO outcomes
            (signal_id, exit_price, pnl, pnl_pct, hold_minutes, closed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            sig_id,
            float(sig["exit_price"]),
            float(sig["pnl"]),
            float(sig["pnl_pct"]),
            int(sig["hold_minutes"]),
            sig["exit_time"].isoformat() if hasattr(sig["exit_time"], 'isoformat') else str(sig["exit_time"]),
        ))
        inserted += 1

    conn.commit()
    return inserted


def run_backfill(max_symbols: int = 20) -> dict:
    """主函數：對最多 max_symbols 支股票生成 backfill 數據。"""
    print(f"=== Historical Backfill ===")
    print(f"Target: up to {max_symbols} symbols, {LOOKBACK_MONTHS} months lookback")

    total_signals = 0
    results = []

    for i, symbol in enumerate(BACKFILL_SYMBOLS[:max_symbols]):
        print(f"[{i+1}/{min(max_symbols, len(BACKFILL_SYMBOLS))}] Processing {symbol}...", end=" ", flush=True)
        df = fetch_history(symbol, LOOKBACK_MONTHS)
        if df is None:
            print("SKIP (insufficient data)")
            continue

        signals = generate_signals(df, symbol)
        if not signals:
            print("0 signals generated")
            continue

        # Insert into DB
        conn = sqlite3.connect(str(DB_PATH))
        try:
            inserted = insert_backfill_data(signals, conn)
            total_signals += inserted
            print(f"{inserted} signals inserted (win={sum(s['label'] for s in signals)})")
            results.append({"symbol": symbol, "signals": inserted, "wins": sum(s['label'] for s in signals)})
        except Exception as e:
            print(f"DB error: {e}")
        finally:
            conn.close()

    print(f"\n=== Backfill Complete ===")
    print(f"Total new signals: {total_signals}")

    # Summary
    if results:
        total_wins = sum(r["wins"] for r in results)
        print(f"Overall win rate: {total_wins}/{total_signals} = {total_wins/max(total_signals,1)*100:.1f}%")

    return {"total_signals": total_signals, "results": results}


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    result = run_backfill(max_symbols=n)
    print(json.dumps(result, indent=2, default=str))
