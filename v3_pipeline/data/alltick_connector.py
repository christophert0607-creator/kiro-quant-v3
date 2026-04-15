"""
Alltick Real-time Stock API Connector
Docs: https://github.com/alltick/alltick-realtime-forex-crypto-stock-tick-finance-websocket-api
"""

import json
import time
import logging
import urllib.request
import urllib.parse
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

ALLTICK_TOKEN = "92c72489df50fc680bcaac8f9f4185d9-c-app"
ALLTICK_BASE = "https://quote.alltick.co/quote-stock-b-api"

# Rate limit: free plan = 1 request per 10 seconds for batch-kline
_last_request_time = 0.0
_request_interval = 11.0  # seconds between requests (10s limit + 1s buffer)


def _rate_limited():
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _request_interval:
        time.sleep(_request_interval - elapsed)
    _last_request_time = time.time()


def _make_request(endpoint: str, payload: dict) -> dict:
    """Make a request to Alltick API with rate limiting."""
    _rate_limited()
    
    trace = f"{int(time.time() * 1000):x}"
    query_data = json.dumps({"trace": trace, "data": payload})
    encoded_query = urllib.parse.quote(query_data, safe="")
    url = f"{ALLTICK_BASE}/{endpoint}?token={ALLTICK_TOKEN}&query={encoded_query}"
    
    req = urllib.request.Request(url)
    req.add_header("Content-Type", "application/json")
    
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode())
    
    if result.get("ret") != 0:
        logger.warning("Alltick API error: ret=%d msg=%s", result.get("ret"), result.get("msg"))
    return result


def get_latest_klines_batch(symbols: list[str], kline_type: int = 1, kline_num: int = 2) -> dict[str, pd.DataFrame]:
    """
    Fetch latest klines for multiple symbols.
    kline_type: 1=1min, 2=5min, 3=15min, 4=30min, 5=hour, 8=daily
    Returns: {symbol: DataFrame with OHLCV data}
    """
    results = {}
    
    # Process in batches of 10 (rate limit safe)
    batch_size = 10
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        
        # Build batch query
        traces = [f"{int(time.time() * 1000):x}-{j}" for j in range(len(batch))]
        symbols_data = []
        for j, sym in enumerate(batch):
            # Convert TSLA -> TSLA.US
            code = sym if "." in sym else f"{sym}.US"
            symbols_data.append({
                "trace": traces[j],
                "data": {
                    "code": code,
                    "kline_type": kline_type,
                    "kline_timestamp_end": 0,
                    "query_kline_num": kline_num,
                    "adjust_type": 0
                }
            })
        
        payload = {"data": {"symbols": symbols_data}}
        _rate_limited()
        
        trace = f"{int(time.time() * 1000):x}"
        query_data = json.dumps({"trace": trace, "data": {"symbols": symbols_data}})
        encoded_query = urllib.parse.quote(query_data, safe="")
        url = f"{ALLTICK_BASE}/batch-kline?token={ALLTICK_TOKEN}&query={encoded_query}"
        
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
            
            if result.get("ret") == 0 and "data" in result:
                data = result["data"]
                for sym, code in zip(batch, [s if "." in s else f"{s}.US" for s in batch]):
                    sym_data = data.get(code, [])
                    if sym_data and len(sym_data) > 0:
                        rows = []
                        for bar in sym_data:
                            rows.append({
                                "Date": pd.Timestamp(bar.get("time"), unit="ms", tz="UTC").tz_convert("Asia/Hong_Kong"),
                                "Open": float(bar.get("open", 0)),
                                "High": float(bar.get("high", 0)),
                                "Low": float(bar.get("low", 0)),
                                "Close": float(bar.get("close", 0)),
                                "Volume": float(bar.get("volume", 0)),
                                "data_source": "ALLTICK"
                            })
                        if rows:
                            results[sym] = pd.DataFrame(rows)
                        else:
                            results[sym] = pd.DataFrame()
                    else:
                        results[sym] = pd.DataFrame()
            else:
                logger.warning("Batch kline failed for %s: ret=%d", batch, result.get("ret"))
                for sym in batch:
                    results[sym] = pd.DataFrame()
        except Exception as exc:
            logger.error("Exception in batch kline for %s: %s", batch, exc)
            for sym in batch:
                results[sym] = pd.DataFrame()
    
    return results


def get_historical_klines(symbol: str, kline_type: int = 1, kline_num: int = 100, days: int = 7) -> pd.DataFrame:
    """
    Fetch historical klines for a single symbol.
    Returns DataFrame with OHLCV data.
    """
    code = symbol if "." in symbol else f"{symbol}.US"
    trace = f"{int(time.time() * 1000):x}"
    payload = {
        "code": code,
        "kline_type": kline_type,
        "kline_timestamp_end": 0,
        "query_kline_num": kline_num,
        "adjust_type": 0
    }
    
    result = _make_request("kline", payload)
    
    if result.get("ret") != 0:
        logger.error("Failed to get klines for %s: ret=%d msg=%s", symbol, result.get("ret"), result.get("msg"))
        return pd.DataFrame()
    
    data = result.get("data", {}).get(code, [])
    if not data:
        return pd.DataFrame()
    
    rows = []
    for bar in data:
        rows.append({
            "Date": pd.Timestamp(bar.get("time"), unit="ms", tz="UTC").tz_convert("Asia/Hong_Kong"),
            "Open": float(bar.get("open", 0)),
            "High": float(bar.get("high", 0)),
            "Low": float(bar.get("low", 0)),
            "Close": float(bar.get("close", 0)),
            "Volume": float(bar.get("volume", 0)),
            "data_source": "ALLTICK"
        })
    
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Date").drop_duplicates(subset=["Date"], keep="last").reset_index(drop=True)
    return df


def get_latest_trade_tick(symbols: list[str]) -> dict[str, dict]:
    """
    Fetch latest trade tick (real-time price) for multiple symbols.
    Returns: {symbol: {"close": float, "open": float, "high": float, "low": float, "volume": float}}
    """
    _rate_limited()
    
    trace = f"{int(time.time() * 1000):x}"
    codes = [s if "." in s else f"{s}.US" for s in symbols]
    symbols_data = [{"code": c} for c in codes]
    
    payload = {"data": {"symbols": [{"code": c} for c in codes]}}
    query_data = json.dumps({"trace": trace, "data": {"symbols": [{"code": c} for c in codes]}})
    encoded_query = urllib.parse.quote(query_data, safe="")
    url = f"{ALLTICK_BASE}/trade-tick?token={ALLTICK_TOKEN}&query={encoded_query}"
    
    results = {}
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
        
        if result.get("ret") == 0 and "data" in result:
            for sym, code in zip(symbols, codes):
                tick_data = result["data"].get(code, {})
                if tick_data:
                    results[sym] = {
                        "Date": pd.Timestamp.now(),
                        "Open": float(tick_data.get("open", 0)),
                        "High": float(tick_data.get("high", 0)),
                        "Low": float(tick_data.get("low", 0)),
                        "Close": float(tick_data.get("close", tick_data.get("last_price", 0))),
                        "Volume": float(tick_data.get("volume", 0)),
                        "data_source": "ALLTICK_TICK"
                    }
                else:
                    results[sym] = None
        else:
            logger.warning("trade-tick failed: ret=%d", result.get("ret"))
            for sym in symbols:
                results[sym] = None
    except Exception as exc:
        logger.error("Exception in get_latest_trade_tick: %s", exc)
        for sym in symbols:
            results[sym] = None
    
    return results


# ---- Test ----
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test historical klines
    print("Testing historical klines for TSLA...")
    df = get_historical_klines("TSLA", kline_type=1, kline_num=50)
    print(f"Got {len(df)} bars")
    if not df.empty:
        print(df.tail(3))
    
    # Test batch klines
    print("\nTesting batch klines for TSLA, NVDA, AAPL...")
    results = get_latest_klines_batch(["TSLA", "NVDA", "AAPL"], kline_type=1, kline_num=2)
    for sym, df in results.items():
        print(f"{sym}: {len(df)} bars, last close={df.iloc[-1]['Close'] if not df.empty else 'N/A'}")
    
    # Test trade tick
    print("\nTesting trade tick for TSLA...")
    ticks = get_latest_trade_tick(["TSLA", "NVDA"])
    for sym, tick in ticks.items():
        print(f"{sym}: {tick}")
