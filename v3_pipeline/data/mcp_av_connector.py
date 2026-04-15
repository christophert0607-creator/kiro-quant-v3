"""
Alpha Vantage MCP connector for V3.
Calls the Alpha Vantage MCP server via mcporter CLI subprocess.

NOTE: Free tier = 5 calls/minute. We enforce 12-second gaps between calls.
"""
import json, subprocess, logging, time, csv, io
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

AV_MCP_SERVER = "alphavantage"
RATE_LIMIT_SEC = 12.0  # Free tier: 5 calls/minute

_last_call_time = 0.0


def _call_mcp(tool_name: str, arguments: dict, timeout: int = 20) -> Optional[str]:
    """Call an Alpha Vantage MCP tool via mcporter CLI. Returns raw text or None."""
    global _last_call_time

    # Rate limit: 5 calls/min = 1 per 12 seconds
    now = time.time()
    elapsed = now - _last_call_time
    if elapsed < RATE_LIMIT_SEC:
        time.sleep(RATE_LIMIT_SEC - elapsed)
    _last_call_time = time.time()

    try:
        args_str = json.dumps(arguments)
        result = subprocess.run(
            [
                "mcporter", "call", f"{AV_MCP_SERVER}.TOOL_CALL",
                f"tool_name={tool_name}",
                f"arguments={args_str}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning("[MCP AV] mcporter failed for %s: %s", tool_name, result.stderr.strip()[:200])
            return None

        output = result.stdout.strip()
        if not output or output.startswith("null") or output.startswith("{}"):
            logger.warning("[MCP AV] Empty response for %s", tool_name)
            return None
        return output
    except subprocess.TimeoutExpired:
        logger.warning("[MCP AV] Timeout for %s", tool_name)
        return None
    except Exception as exc:
        logger.warning("[MCP AV] Exception for %s: %s", tool_name, exc)
        return None


def _parse_csv(raw: str) -> list[dict]:
    """Parse CSV text into list of dicts."""
    try:
        reader = csv.DictReader(io.StringIO(raw))
        return list(reader)
    except Exception:
        return []


def get_realtime_quote(symbol: str) -> Optional[dict]:
    """
    Get real-time quote for a US stock symbol via Alpha Vantage MCP.
    Returns dict with Date, Open, High, Low, Close, Volume.
    """
    raw = _call_mcp("global_quote", {"symbol": symbol})
    if raw is None:
        return None

    rows = _parse_csv(raw)
    if not rows:
        return None

    r = rows[0]
    try:
        return {
            "Date": pd.Timestamp.now(),
            "Open": float(r.get("open", 0) or 0),
            "High": float(r.get("high", 0) or 0),
            "Low": float(r.get("low", 0) or 0),
            "Close": float(r.get("price", 0) or 0),
            "Volume": float(r.get("volume", 0) or 0),
            "data_source": "AV_MCP",
        }
    except (ValueError, KeyError) as exc:
        logger.warning("[MCP AV] Failed to parse global_quote for %s: %s", symbol, exc)
        return None


def get_daily_bars(symbol: str, outputsize: str = "compact") -> pd.DataFrame:
    """
    Get daily OHLCV bars. outputsize='compact' (100 days) or 'full' (20+ years).
    """
    raw = _call_mcp("time_series_daily", {"symbol": symbol, "outputsize": outputsize}, timeout=30)
    if raw is None:
        return pd.DataFrame()

    rows = _parse_csv(raw)
    if not rows:
        return pd.DataFrame()

    records = []
    for r in rows:
        try:
            records.append({
                "Date": pd.Timestamp(r.get("timestamp", r.get("date", ""))),
                "Open": float(r.get("open", 0) or 0),
                "High": float(r.get("high", 0) or 0),
                "Low": float(r.get("low", 0) or 0),
                "Close": float(r.get("close", 0) or 0),
                "Volume": float(r.get("volume", 0) or 0),
                "data_source": "AV_MCP_DAILY",
            })
        except (ValueError, KeyError):
            continue

    df = pd.DataFrame(records)
    if not df.empty:
        df = df.sort_values("Date").drop_duplicates(subset=["Date"], keep="last").reset_index(drop=True)
        df["Dividends"] = 0.0
        df["Stock Splits"] = 0.0
    return df


def get_rsi(symbol: str, interval: str = "daily", time_period: int = 14) -> Optional[list[dict]]:
    """Get RSI technical indicator."""
    raw = _call_mcp(
        "rsi",
        {"symbol": symbol, "interval": interval, "time_period": str(time_period), "series_type": "close"},
    )
    if raw is None:
        return None
    rows = _parse_csv(raw)
    if not rows:
        return None
    result = []
    for r in rows:
        try:
            result.append({"date": r.get("date", ""), "rsi": float(r.get("rsi", 0) or 0)})
        except (ValueError, KeyError):
            continue
    return result


# ── Test ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== Alpha Vantage MCP Test ===\n")

    print("TSLA real-time quote:")
    q = get_realtime_quote("TSLA")
    print(f"  {q}\n")

    print("AAPL daily bars (compact):")
    df = get_daily_bars("AAPL", "compact")
    print(f"  {len(df)} rows, {df.iloc[0]['Date']} → {df.iloc[-1]['Date']}")
    print(f"  Last: Close={df.iloc[-1]['Close']} Vol={df.iloc[-1]['Volume']}\n")

    print("IBM RSI:")
    rsi = get_rsi("IBM")
    if rsi:
        print(f"  Last 3: {rsi[-3:]}")
