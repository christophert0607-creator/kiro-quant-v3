"""Technical + Sentiment Screener for KiroQuant V3.

Ranks and filters symbols by composite technical score so the trading loop
can focus capital on the best setups each cycle.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = os.environ.get(
    "KIRO_PROJECT_ROOT",
    os.path.join(os.path.dirname(__file__), "..", "..", ".."),
)


@dataclass
class ScreenConfig:
    """Thresholds for the screener."""

    # RSI bounds (outside these = filtered out)
    rsi_min: float = 30.0
    rsi_max: float = 70.0

    # MACD (0 = histogram crossing zero, >0 = bullish)
    macd_hist_min: float = 0.0

    # Volume: current vol / 20-bar avg vol
    vol_ratio_min: float = 0.0  # disabled: volume always varies vs 20d avg

    # Price above SMA20 (trend confirmation)
    price_vs_sma20: bool = True

    # Sentiment minimum
    sentiment_min: float = 0.1

    # Composite score weights
    w_rsi: float = 0.20   # distance from oversold (higher = better)
    w_macd: float = 0.15  # MACD histogram normalised
    w_vol: float = 0.15   # volume ratio
    w_trend: float = 0.20  # price vs SMA20
    w_sentiment: float = 0.30  # sentiment score

    # Max symbols to return
    top_n: int = 5

    # Min confidence to consider
    confidence_min: float = 0.001

    @classmethod
    def from_env(cls) -> "ScreenConfig":
        return cls(
            rsi_min=float(os.getenv("SCREEN_RSI_MIN", "30")),
            rsi_max=float(os.getenv("SCREEN_RSI_MAX", "70")),
            macd_hist_min=float(os.getenv("SCREEN_MACD_MIN", "0")),
            vol_ratio_min=float(os.getenv("SCREEN_VOL_MIN", "1.0")),
            price_vs_sma20=os.getenv("SCREEN_PRICE_VS_SMA20", "1") != "0",
            sentiment_min=float(os.getenv("SCREEN_SENTIMENT_MIN", "0.1")),
            w_rsi=float(os.getenv("SCREEN_W_RSI", "0.2")),
            w_macd=float(os.getenv("SCREEN_W_MACD", "0.15")),
            w_vol=float(os.getenv("SCREEN_W_VOL", "0.15")),
            w_trend=float(os.getenv("SCREEN_W_TREND", "0.2")),
            w_sentiment=float(os.getenv("SCREEN_W_SENTIMENT", "0.3")),
            top_n=int(os.getenv("SCREEN_TOP_N", "5")),
            confidence_min=float(os.getenv("SCREEN_CONFIDENCE_MIN", "0.0")),
        )


def _zscore(series: pd.Series) -> pd.Series:
    """Normalise a series to z-scores, capped at ±2."""
    mu = series.mean()
    sigma = series.std()
    if sigma < 1e-9:
        return pd.Series(0.0, index=series.index)
    z = (series - mu) / sigma
    return z.clip(-2, 2)


def score_symbols(
    buffers: dict[str, pd.DataFrame],
    confidence: dict[str, float],
    sentiment: dict[str, float],
    config: Optional[ScreenConfig] = None,
) -> list[tuple[str, float]]:
    """Score and rank symbols. Returns [(symbol, composite_score), ...] sorted desc.

    Args:
        buffers: market_buffers dict — symbol -> OHLCV DataFrame (with indicators)
        confidence: {symbol: model confidence}
        sentiment: {symbol: sentiment score}
        config: ScreenConfig (defaults to ScreenConfig.from_env())

    Returns:
        sorted list of (symbol, score) pairs, highest first
    """
    if config is None:
        config = ScreenConfig.from_env()

    rows = []

    for symbol, df in buffers.items():
        if df.empty or len(df) < 25:
            continue

        latest = df.iloc[-1]

        # ── Indicators ──────────────────────────────────────────
        rsi_raw = latest.get("RSI_14")
        try:
            rsi = float(rsi_raw)
        except (TypeError, ValueError):
            rsi = 50.0  # default if NaN
        macd_hist_raw = latest.get("MACD_HIST")
        try:
            macd_hist = float(macd_hist_raw)
        except (TypeError, ValueError):
            macd_hist = 0.0
        sma20 = float(latest.get("SMA_20", 0))
        close = float(latest.get("Close", 0))
        volume = float(latest.get("Volume", 0))

        # Volume ratio vs 20-bar average
        vol_20avg = df["Volume"].tail(20).mean()
        vol_ratio = volume / vol_20avg if vol_20avg > 0 else 0.0

        # ── Filters (pass = True) ─────────────────────────────────
        if config.rsi_min <= rsi <= config.rsi_max:
            rsi_ok = True
        else:
            rsi_ok = rsi < config.rsi_min   # oversold counts as good for filter
        if not rsi_ok:
            continue

        if macd_hist < config.macd_hist_min and config.macd_hist_min > 0:
            continue

        if config.vol_ratio_min > 0 and vol_ratio < config.vol_ratio_min:
            continue

        if config.price_vs_sma20 and sma20 > 0 and close < sma20:
            continue

        sym_confidence = confidence.get(symbol, 0.0)
        if sym_confidence < config.confidence_min:
            continue

        sym_sentiment = sentiment.get(symbol, 0.0)
        if sym_sentiment < config.sentiment_min:
            continue

        # ── Composite Score ──────────────────────────────────────
        # RSI score: 50 = neutral, higher = closer to oversold bounce
        rsi_score = (rsi - config.rsi_min) / (config.rsi_max - config.rsi_min) if (config.rsi_max - config.rsi_min) > 0 else 0.0

        # MACD score: normalise by typical range ±0.5
        macd_score = float(np.clip((macd_hist + 0.5) / 1.0, 0, 1))

        # Volume score
        vol_score = float(np.clip(vol_ratio / 2.0, 0, 1))

        # Trend score: distance price is above SMA20
        if sma20 > 0:
            trend_score = float(np.clip((close - sma20) / sma20 * 5 + 0.5, 0, 1))
        else:
            trend_score = 0.5

        # Sentiment score
        sent_score = float(np.clip(sym_sentiment, 0, 1))

        total_w = config.w_rsi + config.w_macd + config.w_vol + config.w_trend + config.w_sentiment
        composite = (
            config.w_rsi / total_w * rsi_score
            + config.w_macd / total_w * macd_score
            + config.w_vol / total_w * vol_score
            + config.w_trend / total_w * trend_score
            + config.w_sentiment / total_w * sent_score
        )

        rows.append((symbol, composite, rsi_score, macd_score, vol_score, trend_score, sent_score, rsi, macd_hist, vol_ratio, close, sma20, sym_confidence, sym_sentiment))

    # Sort descending by composite
    rows.sort(key=lambda x: x[1], reverse=True)

    result = [(r[0], r[1]) for r in rows]

    return result


def print_screener_report(
    ranked: list[tuple[str, float]],
    buffers: dict[str, pd.DataFrame],
    confidence: dict[str, float],
    sentiment: dict[str, float],
    config: Optional[ScreenConfig] = None,
) -> None:
    """Pretty-print screener report for logging."""
    if config is None:
        config = ScreenConfig.from_env()

    if not ranked:
        print("[SCREENER] No symbols passed filters")
        return

    print(f"[SCREENER] Top {min(len(ranked), config.top_n)} candidates:")
    print(
        f"{'Symbol':<12} {'Score':>6}  {'RSI':>6} {'MACD':>7} {'VolR':>5} {'Trend':>6} {'Senti':>6}  {'Close':>8}  {'Confidence':>10}"
    )
    print("-" * 90)

    for symbol, composite in ranked[: config.top_n]:
        df = buffers.get(symbol)
        if df is None or df.empty:
            continue
        latest = df.iloc[-1]
        rsi = latest.get("RSI_14", 0)
        macd = latest.get("MACD_HIST", 0)
        vol_20avg = df["Volume"].tail(20).mean()
        vol_ratio = float(latest["Volume"]) / vol_20avg if vol_20avg > 0 else 0
        sma20 = latest.get("SMA_20", 0)
        close = latest.get("Close", 0)
        conf = confidence.get(symbol, 0)
        sent = sentiment.get(symbol, 0)

        print(
            f"{symbol:<12} {composite:>6.3f}  "
            f"{rsi:>6.1f} {macd:>7.4f} {vol_ratio:>5.2f} "
            f"{((close-sma20)/sma20*100) if sma20 > 0 else 0:>+6.2f}% "
            f"{sent:>6.3f}  {close:>8.4f}  {conf:>10.4f}"
        )


# ── CLI test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    # Load fake buffers from a decision trace snapshot
    trace_path = os.path.join(PROJECT_ROOT, "learning", "us_sim", "decision_trace_us_sim.jsonl")
    if not os.path.exists(trace_path):
        print(f"[SCREENER] No trace file at {trace_path}")
        sys.exit(1)

    # Build buffers + metadata from last N lines of trace
    from collections import defaultdict

    latest_by_symbol: dict[str, dict] = {}
    for line in open(trace_path):
        d = json.loads(line)
        sym = d.get("symbol")
        if sym:
            latest_by_symbol[sym] = d

    buffers: dict[str, pd.DataFrame] = {}
    confidence: dict[str, float] = {}
    sentiment: dict[str, float] = {}

    for sym, d in latest_by_symbol.items():
        ind = d.get("indicators", {})
        buffers[sym] = pd.DataFrame([{
            "Close": d.get("close", 100),
            "RSI_14": ind.get("rsi", 50),
            "MACD_HIST": ind.get("macd", 0),
            "SMA_20": d.get("close", 100) * 0.99,
            "Volume": ind.get("volume", 1_000_000),
        }])
        confidence[sym] = d.get("confidence", 0)
        sentiment[sym] = d.get("sentiment", 0)

    config = ScreenConfig.from_env()
    ranked = score_symbols(buffers, confidence, sentiment, config)
    print_screener_report(ranked, buffers, confidence, sentiment, config)

# ── V3-Dynamic-Screener (Funnel Logic) ──────────────────────────────────
import json
from datetime import datetime

class V3FunnelScreener:
    """
    移植自 openStock 的選股漏斗邏輯。
    全市場股票 -> [業績成長] -> [估值合理] -> [短期趨勢] -> 今日潛力名單
    """
    def __init__(self, watchlist_path="dynamic_watchlist.json"):
        from pathlib import Path
        base_dir = Path(__file__).parent.parent.parent
        self.watchlist_path = base_dir / watchlist_path

    def fetch_data(self, symbols: list[str]) -> pd.DataFrame:
        import yfinance as yf
        from concurrent.futures import ThreadPoolExecutor, as_completed
        data = []
        failed = []
        print(f"[V3FunnelScreener] Fetching data for {len(symbols)} symbols (concurrent)...")
        
        def _fetch_one(s):
            try:
                t = yf.Ticker(s)
                info = t.info
                return {
                    "symbol": s,
                    "pe": info.get("forwardPE"),
                    "eps_growth": info.get("earningsQuarterlyGrowth"),
                    "rev_growth": info.get("revenueGrowth"),
                    "peg": info.get("pegRatio"),
                    "price": info.get("currentPrice"),
                    "ma50": info.get("fiftyDayAverage"),
                }
            except Exception as e:
                return {"_failed": s, "_error": str(e)}
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_one, s): s for s in symbols}
            for future in as_completed(futures):
                result = future.result()
                if "_failed" in result:
                    failed.append({"symbol": result["_failed"], "error": result["_error"]})
                    print(f"[V3FunnelScreener] Warning: Failed to fetch {result['_failed']}: {result['_error']}")
                else:
                    data.append(result)
        self._failed_symbols = failed
        return pd.DataFrame(data)

    def filter_by_growth(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty: return df
        mask = (df['eps_growth'].fillna(0) > 0.1) | (df['rev_growth'].fillna(0) > 0.1)
        return df[mask]

    def filter_by_valuation(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty: return df
        mask = (df['pe'].fillna(999) < 30) | (df['peg'].fillna(999) < 2)
        return df[mask]

    def filter_by_trend(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty: return df
        mask = df['price'] > df['ma50']
        return df[mask]

    def run(self, symbols: list[str]):
        df = self.fetch_data(symbols)
        df = self.filter_by_growth(df)
        df = self.filter_by_valuation(df)
        df = self.filter_by_trend(df)

        picks = df['symbol'].tolist()
        details = df.to_dict(orient="records")
        failed = getattr(self, '_failed_symbols', [])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Preserve the tiered schema written by v3_pipeline/config/universe.py and
        # asserted by tests/test_universe_expansion.py::test_dynamic_watchlist_format.
        # Load whatever's already there, merge in today's picks (push them into
        # tier1.symbols + the legacy top-level `picks` array), and keep version /
        # tiers / metadata so PR #85's universe loader doesn't fall back to legacy.
        existing: dict = {}
        try:
            if self.watchlist_path.exists():
                with open(self.watchlist_path, "r", encoding="utf-8") as f:
                    existing = json.load(f) or {}
        except (OSError, json.JSONDecodeError):
            existing = {}

        tiers = existing.get("tiers")
        if not isinstance(tiers, dict):
            tiers = {
                "tier1": {
                    "description": "Active trading — screener picks, model evaluation, order execution",
                    "max_size": 20,
                    "symbols": [],
                },
                "tier2": {
                    "description": "Full universe — data collection, backfill, indicator computation",
                    "count": 0,
                    "symbols": [],
                },
                "tier3": {
                    "description": "Watch only — symbols pending promotion / under review",
                    "symbols": [],
                },
            }

        tier1 = tiers.setdefault("tier1", {"description": "Active trading", "max_size": 20, "symbols": []})
        max_size = int(tier1.get("max_size", 20) or 20)
        tier1["symbols"] = picks[:max_size]
        tiers["tier1"] = tier1
        tiers.setdefault("tier2", {"description": "Full universe", "count": 0, "symbols": []})
        tiers.setdefault("tier3", {"description": "Watch only", "symbols": []})

        metadata = existing.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["last_screened_at"] = now
        metadata["last_screener"] = "V3FunnelScreener"
        metadata["picks_count"] = len(picks)
        metadata["failed_count"] = len(failed)

        results = {
            "version": existing.get("version", "2.0"),
            "date": now,
            "expanded_universe": bool(existing.get("expanded_universe", False)),
            "tiers": tiers,
            "metadata": metadata,
            # Legacy fields retained for backward compatibility with older callers
            "picks": picks,
            "details": details,
            "failed_symbols": failed,
        }

        with open(self.watchlist_path, "w") as f:
            json.dump(results, f, indent=4)

        print(f"[V3FunnelScreener] Screening complete. {len(picks)} picks saved to {self.watchlist_path}")
        return picks
