#!/usr/bin/env python3
"""Launcher for Kiro V3 live paper auto-trading mode with market auto-switching."""

import asyncio
import json
import os
import socket
import threading
import time  # 2026-04-24: for cooldown
import argparse
import logging
import pandas as pd
from dataclasses import replace
from datetime import datetime, time as dt_time_cls, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# Load .env manually
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from v3_pipeline.core.futu_connector import FutuConnector
from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop
from v3_pipeline.models.brain import KiroLSTM
from v3_pipeline.models.manager import DataPreparer, ModelManager
from v3_pipeline.risk.manager import RiskController
from v3_pipeline.core.market_analyzer import MarketAnalyzer
from v3_pipeline.features.screener import V3FunnelScreener

HK_TZ = ZoneInfo("Asia/Hong_Kong")
_CRASH_COUNT_FILE = Path(__file__).parent / ".crash_count"


def _get_crash_count() -> int:
    try:
        return int(_CRASH_COUNT_FILE.read_text().strip())
    except Exception:
        return 0


def _save_crash_count(n: int) -> None:
    try:
        _CRASH_COUNT_FILE.write_text(str(n))
    except Exception:
        pass


def _reset_crash_count() -> None:
    try:
        _CRASH_COUNT_FILE.unlink(missing_ok=True)
    except Exception:
        pass


HK_SYMBOLS = [
    "0700.HK", "9988.HK", "3690.HK", "1024.HK", "2318.HK", "1299.HK", "0939.HK",
    "0005.HK", "0388.HK", "0960.HK", "1109.HK", "0941.HK", "0175.HK", "1810.HK",
    "2688.HK", "2269.HK", "1211.HK", "2018.HK", "0688.HK",
]
US_SYMBOLS = [
    "A", "AA", "AAL", "AAP", "AAPL", "ABBV", "ABNB", "ABT",
    "ACGL", "ACN", "ACT", "ADBE", "ADCT", "ADI", "ADM", "ADP",
    "ADS", "ADSK", "ADT", "AEE", "AEP", "AES", "AFL", "AIG",
    "AIV", "AIZ", "AJG", "AKAM", "ALB", "ALGN", "ALK", "ALL",
    "ALLE", "ALNY", "AMAT", "AMCR", "AMD", "AME", "AMG", "AMGN",
    "AMP", "AMT", "AMTM", "AMZN", "AN", "ANET", "ANF", "AON",
    "AOS", "APA", "APC", "APD", "APH", "APO", "APP", "APTV",
    "ARE", "ARES", "ARM", "ASML", "ATI", "ATO", "AVB", "AVGO",
    "AVY", "AWK", "AXON", "AXP", "AYI", "AZN", "AZO", "BA",
    "BAC", "BALL", "BATRA", "BATRK", "BAX", "BCE", "BBY", "BC", "BDX",
    "BEAM", "BEAS", "BEN", "BG", "BHI", "BIDU", "BIG", "BIIB",
    "BIO", "BK", "BKNG", "BKR", "BLDR",
    "BLK", "BMC", "BMET", "BMRN", "BMY", "BR", "BRCM", "BS",
    "BSX", "BTU", "BUD", "BWA", "BX", "BXLT", "BXP", "C",
    "CA", "CAM", "CARR", "CAT", "CCE", "CBE", "CBOE", "CBRE",
    "CCEP", "CCI", "CCK", "CCL", "CCR", "CDAY", "CDNS", "CDW",
    "CDWC", "CE", "CEG", "CEPH", "CF", "CFC", "CFG", "CFN",
    "CHK", "CHKP", "CHTR", "CIEN", "CIK", "CINF", "CKFR", "CL",
    "CLF", "CMA", "CMCSA", "CMCSK", "CME", "CMG", "CMS", "CMVT",
    "CNX", "COF", "COHR", "COIN", "COL", "COO", "COR", "COST",
    "COTY", "CPRI", "CPRT", "CRH", "CRL", "CRM", "CRWD", "CSC",
    "CSCO", "CSGP", "CSRA", "CSX", "CTAS", "CTLT", "CTRA", "CTRP",
    "CTRX", "CTSH", "CTVA", "CVNA", "CVX", "CZR", "DASH", "DAY",
    "DDOG", "DECK", "DELL", "DF", "DG", "DHI", "DHR", "DIS",
    "DISH", "DJ", "DLR", "DLTR", "DOC", "DOCU", "DOV", "DPS",
    "DRE", "DTE", "DUK", "DVN", "DYN", "EA", "ECL", "ED",
    "EFX", "EL", "ELV", "EMC", "EME", "EMN", "ENPH", "EOG",
    "EPAM", "EQIX", "EQR", "EQT", "ESS", "ETSY", "EVHC", "EVRG",
    "EXC", "EXE", "EXPD", "EXPE", "EXR", "F", "FANG", "FAST",
    "FB", "FBHS", "FCX", "FDO", "FDS", "FDX", "FE", "FER",
    "FFIV", "FHN", "FI", "FICO", "FITB", "FIX", "FL", "FLEX",
    "FLR", "FLS", "FLT", "FMCN", "FNM", "FOSL", "FOX", "FOXA",
    "FRE", "FRT", "FRX", "FSLR", "FSR", "FTI", "FTNT", "FWLT",
    "GDDY", "GEHC", "GEN", "GENZ", "GEV", "GFS", "GHC",
    "GILD", "GIS", "GL", "GLK", "GM", "GMCR", "GME", "GNRC",
    "GOLD", "GOOG", "GOOGL", "GPC", "GPS", "GRA", "GRMN",
    "GRN", "GT", "HANS", "HBAN", "HCBK", "HLT", "HNG", "HNZ",
    "HOG", "HOLX", "HOOD", "HOT", "HP", "HPH", "HRL", "HSIC",
    "HSP", "HST", "HUBB", "HWM", "IACI", "IBKR", "IEX", "INFO",
    "INFY", "INSM", "INTC", "INTU", "INVH", "IP", "IPG", "IPGP",
    "IQV", "IQVIA", "IRM", "IT", "ITT", "J", "JAVA", "JBL",
    "JD", "JDSU", "JEC", "JEF", "JNJ", "JNS", "JNY",
    "JOYG", "KBH", "KDP", "KEY", "KEYS", "KFT", "KG", "KIM",
    "KKR", "KLAC", "KO", "KORS", "KR", "KRFT", "KSE", "KSU",
    "KVUE", "L", "LAMR", "LBTYA", "LBTYK", "LCID", "LDOS", "LDW",
    "LEAP", "LEG", "LEH", "LEN", "LIFE", "LII", "LILA", "LILAK",
    "LIN", "LITE", "LKQ", "LLY", "LMCA", "LMCK", "LNT", "LO",
    "LOGI", "LSI", "LULU", "LUMN", "LVLT", "LVS", "LW", "LXK",
    "LYB", "LYV", "MAR", "MAT", "MBC", "MBI", "MBIA", "MDB",
    "MDLZ", "MDP", "MEDI", "MEE", "MELI", "META", "MFE", "MHK",
    "MHS", "MI", "MICC", "MIL", "MKTX", "MMI", "MMM", "MNST",
    "MOH", "MOLX", "MON", "MOS", "MPWR", "MRK", "MRNA", "MRO",
    "MRSH", "MRVL", "MSFT", "MSTR", "MTB", "MTCH", "MWW", "MXIM",
    "MYL", "NAVI", "NBR", "NCC", "NDOI", "NE", "NIHD", "NKE",
    "NKTR", "NLOK", "NLSN", "NLTI", "NOW", "NRG", "NTES",
    "NUAN", "NVDA", "NVLS", "NVR", "NXP", "NXPI", "NYT", "NYX",
    "O", "ODFL", "ODP", "OGN", "OI", "OKE", "OKTA", "OMX",
    "ON", "OTIS", "PANW", "PAYC", "PBI", "PCAR", "PCG", "PCL",
    "PCLN", "PCS", "PDD", "PENN", "PETM", "PGN", "PLL", "PLTR",
    "PNC", "POM", "PNR", "PODD", "POOL", "PPDI", "PPL", "PSKY", "PSX",
    "PTEN", "PTON", "PTV", "PYPL", "QEP", "QGEN", "QRTEA", "QRVO",
    "QTRN", "RAD", "RAI", "RE", "RIG", "RIVN",
    "RJF", "RRD", "RSH", "RTX", "RVTY", "RX", "RYAAY", "SAI",
    "SATS", "SBL", "SBNY", "SCANA", "SCG", "SCHW", "SEDG", "SEPR",
    "SGEN", "SGP", "SHOP", "SHPG", "SII", "SIRI",
    "SLE", "SLR", "SMCI", "SMS", "SNDK", "SOLS", "SOLV",
    "SPLK", "SPLS", "STE", "STJ", "STLD", "STR", "STRZA", "STT",
    "STX", "SW", "SWKS", "SWN", "SWY", "SYF", "TCFCA", "TCFCB",
    "TCOM", "TDG", "TDY", "TEAM", "TECH", "TEG", "TER", "TEVA",
    "TFX", "TGNA", "TIE", "TKO", "TMC", "TMUS", "TPL", "TPR",
    "TRGP", "TRI", "TRIP", "TRMB", "TROW", "TRV", "TSCO",
    "TSG", "TSLA", "TSN", "TSO", "TSYS", "TTD", "TWC", "TWTR",
    "TXN", "TYC", "TYL", "UA", "UAA", "UAL", "UAUA", "UBER",
    "UDR", "UK", "UNP", "UPS", "URBN", "US", "USL", "VIAB",
    "VICI", "VIP", "VLTO", "VMED", "VNT", "VOD", "VRSN", "VRT",
    "VSNT", "VST", "VTRS", "VZ",    "WAB", "WB", "WBD", "WL", "WCRX",
    "WDAY", "WDC", "WELL", "WFMI", "WFR", "WIN", "WLP", "WPX",
    "WRB", "WSM", "WTW", "WYNN", "XMSR", "XTO", "XYZ", "ZM",
    "ZS",
]
# Top 50 most liquid US stocks for IDLE precompute (Stabilized 2026-05-04)
TOP_PRECOMPUTE_US = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "AVGO", "ORCL",
    "CRM", "ADBE", "CSCO", "ACN", "IBM", "INTC", "QCOM", "TXN", "AMAT", "MU",
    "NFLX", "COIN", "MSTR", "UBER", "DASH", "SNOW", "PANW", "CRWD", "ZS", "NET",
    "PLTR", "ARM", "SMCI", "TSM", "V", "MA", "JPM", "BAC", "LLY", "UNH",
    "WMT", "COST", "PG", "JNJ", "HD", "XOM", "CVX", "MRK", "ABBV", "KO"
]
IDLE_COLLECTION_SYMBOLS = HK_SYMBOLS + TOP_PRECOMPUTE_US


def _read_config(config_path: str = "config.json") -> tuple[dict, Path]:
    cfg = {}
    p = Path(config_path)
    if p.exists():
        cfg = json.loads(p.read_text(encoding="utf-8"))
    return cfg, p


def _base_live_config(config_path: str = "config.json") -> LiveConfig:
    cfg, _ = _read_config(config_path)
    v3_live = cfg.get("v3_live", {}) if isinstance(cfg, dict) else {}
    capital_buckets = v3_live.get("capital_buckets", {}) if isinstance(v3_live, dict) else {}
    bucket_fractions = capital_buckets.get(
        "fractions",
        {"long": 0.5, "mid": 0.3, "short": 0.1, "reserve": 0.1},
    )
    bucket_by_symbol = capital_buckets.get("by_symbol", {})
    bucket_thresholds = capital_buckets.get(
        "thresholds",
        {"long": 0.0020, "mid": 0.0015, "short": 0.0012, "avoid": 0.01},
    )

    return LiveConfig(
        symbols_list=v3_live.get("symbols_list", US_SYMBOLS.copy()),
        polling_seconds=int(v3_live.get("polling_seconds", 60)),
        prediction_threshold=0.01,
        prediction_thresholds=v3_live.get(
            "prediction_thresholds",
            {"TSLA": 0.01, "TSLL": 0.01, "NVDA": 0.01},
        ),
        auto_trade=bool(v3_live.get("auto_trade", cfg.get("auto_trade", True))),
        paper_trading=bool(v3_live.get("paper_trading", cfg.get("paper_trading", True))),
        max_orders_per_cycle=int(v3_live.get("max_orders_per_cycle", 3)),
        order_throttle_seconds=float(v3_live.get("order_throttle_seconds", 30.0)),
        swing_buy_min_confidence=float(v3_live.get("swing_buy_min_confidence", 0.45)),
        model_buy_min_confidence=float(v3_live.get("model_buy_min_confidence", 0.55)),
        buy_cooldown_cycles=int(v3_live.get("buy_cooldown_cycles", 3)),
        bucket_fractions=bucket_fractions,
        bucket_by_symbol=bucket_by_symbol,
        bucket_thresholds=bucket_thresholds,
        max_portfolio_positions=int(v3_live.get("max_positions", 8)),
        min_confidence_threshold=float(v3_live.get("xgb_confidence", 0.10)),  # 2026-04-23: default 0.10
        rsi_oversold_entry=int(v3_live.get("rsi_oversold", 50)),  # 2026-04-23: default 50
        rsi_extreme_oversold=int(v3_live.get("rsi_extreme_oversold", 35)),  # 2026-04-23: NEW
        bb_position_threshold=float(v3_live.get("bb_position_threshold", 0.2)),  # 2026-04-23: NEW
        vix_dynamic_confidence_enabled=bool(v3_live.get("vix_dynamic_confidence_enabled", True)),  # 2026-04-23: NEW
        min_hold_minutes=int(v3_live.get("min_hold_minutes", 45)),  # 2026-04-23: default 45
        time_exit_near_entry_pct=float(v3_live.get("time_exit_near_entry_pct", 0.005)),  # 2026-04-23: default 0.005
        sentiment_min_entry=float(v3_live.get("sentiment_min_entry", -0.5)),
        stop_loss=float(v3_live.get("stop_loss_pct", 0.015)),
        take_profit_normal=float(v3_live.get("quick_take_profit_pct", 0.02)),
        short_enabled=bool(v3_live.get("short_enabled", True)),
        rsi_overbought_entry=int(v3_live.get("rsi_overbought", 70)),
        rsi_deep_overbought=int(v3_live.get("rsi_deep_overbought", 80)),
        macd_negative_required=bool(v3_live.get("macd_negative_required", True)),
        sma_filter_short=bool(v3_live.get("sma_filter_short", True)),
        short_min_confidence_threshold=float(v3_live.get("short_min_confidence_threshold", 0.10)),  # 2026-04-23: default 0.10
        short_sentiment_max=float(v3_live.get("short_sentiment_max", 0.10)),
        short_stop_loss=float(v3_live.get("short_stop_loss", 0.015)),
        short_take_profit=float(v3_live.get("short_take_profit", 0.02)),
        short_trailing_stop_trigger=float(v3_live.get("short_trailing_stop_trigger", 0.015)),
        short_trailing_stop_lock=float(v3_live.get("short_trailing_stop_lock", 0.0)),
        hk_bypass_kelly_zero_edge=bool(v3_live.get("hk_bypass_kelly_zero_edge", True)),
    )


def build_live_config(config_path: str = "config.json") -> LiveConfig:
    base_cfg = _base_live_config(config_path)
    use_dynamic = os.getenv("USE_DYNAMIC_WATCHLIST", "1").strip().lower()
    if use_dynamic not in {"0", "false", "no"}:
        watchlist_path = Path(__file__).parent / 'dynamic_watchlist.json'
        if watchlist_path.exists():
            try:
                wdata = json.loads(watchlist_path.read_text())
                if wdata.get('picks'):
                    picks = wdata['picks']
                    # Don't inject US-only dynamic picks when current mode is HK.
                    # HK symbols end with ".HK"; US picks never do.
                    current_mode = resolve_market_mode()
                    picks_are_us_only = picks and not any(
                        str(p).upper().endswith(".HK") for p in picks
                    )
                    if current_mode == "HK" and picks_are_us_only:
                        print('[launcher] Skipping dynamic watchlist injection — HK mode active')
                    else:
                        print(f'[launcher] Injecting Dynamic Watchlist: {picks}')
                        base_cfg = replace(base_cfg, symbols_list=picks)
            except Exception:
                pass
    else:
        print('[launcher] USE_DYNAMIC_WATCHLIST=0 — skipping dynamic watchlist injection')
    return base_cfg


def _in_range(now_t: dt_time_cls, start: dt_time_cls, end: dt_time_cls) -> bool:
    if start <= end:
        return start <= now_t <= end
    return now_t >= start or now_t <= end


def resolve_market_mode(now: datetime | None = None) -> str:
    now = now or datetime.now(HK_TZ)
    weekday = now.weekday()  # 0=Monday, 6=Sunday
    hk_time = now.timetz().replace(tzinfo=None)

    # HK Market: 09:30-12:00 (morning session), 13:00-16:00 (afternoon session).
    # Only applies on weekdays — weekends never touch HK hours.
    if weekday < 5:
        is_hk_trading = (
            (dt_time_cls(9, 30) <= hk_time <= dt_time_cls(12, 0))
            or (dt_time_cls(13, 0) <= hk_time <= dt_time_cls(16, 0))
        )
        if is_hk_trading:
            return "HK"

    # US Market (approx HKT): 21:30 – 04:00 next day.
    # Weekend cross-midnight rules:
    #   - Sat before 04:00 HKT → still Friday US session → US
    #   - Sat after  04:00 HKT → US market closed for weekend → IDLE
    #   - Sun before 21:30 HKT → US not yet open → IDLE
    #   - Sun after  21:30 HKT → Monday US pre-market / session starts → US
    # The old "if weekday >= 5: return IDLE" guard was placed before this block,
    # making the Saturday-before-04:00 and Sunday-after-21:30 branches unreachable.
    is_us_trading = (hk_time >= dt_time_cls(21, 30)) or (hk_time <= dt_time_cls(4, 0))
    if is_us_trading:
        if weekday == 5 and hk_time > dt_time_cls(4, 0):
            return "IDLE"
        if weekday == 6 and hk_time < dt_time_cls(21, 30):
            return "IDLE"
        return "US"

    return "IDLE"


def _symbols_for_mode(mode: str) -> list[str]:
    if mode == "HK":
        return HK_SYMBOLS.copy()
    if mode == "US":
        us_universe = str(os.getenv("US_UNIVERSE", "top")).strip().lower()
        if us_universe in {"top", "liquid", "small"}:
            return TOP_PRECOMPUTE_US.copy()
        return US_SYMBOLS.copy()
    return []


def _write_market_config(mode: str, config_path: str = "config.json") -> None:
    cfg, path = _read_config(config_path)
    if not isinstance(cfg, dict):
        cfg = {}
    v3_live = cfg.setdefault("v3_live", {})
    v3_live["symbols_list"] = _symbols_for_mode(mode)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_symbol_state(loop: "LiveTradingLoop", symbol: str) -> None:
    import pandas as pd

    loop.market_buffers.setdefault(
        symbol,
        pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume", "data_source"]),
    )
    loop.position_qty_by_symbol.setdefault(symbol, 0)
    loop.highest_price_since_entry_by_symbol.setdefault(symbol, 0.0)
    loop.bars_held_by_symbol.setdefault(symbol, 0)
    loop.cycles_since_buy_by_symbol.setdefault(symbol, 999999)
    loop.entry_price_by_symbol.setdefault(symbol, 0.0)
    loop.data_preparers_by_symbol.setdefault(
        symbol,
        DataPreparer(
            lookback=loop.model_manager.data_preparer.lookback,
            target_col=loop.model_manager.data_preparer.target_col,
        ),
    )


_DEFAULT_MARKET_MODELS = {
    "HK": "v3_hk_stocks",
    "US": "v3_us_stocks",
    "IDLE": "v3_us_stocks",
}


def _model_name_for_market(mode: str, config_path: str = "config.json") -> str:
    """Resolve the logical model name for ``mode`` from config, with a default."""
    cfg, _ = _read_config(config_path)
    markets = (cfg.get("model") or {}).get("markets") or {}
    return str(markets.get(mode) or _DEFAULT_MARKET_MODELS.get(mode, "v3_us_stocks"))


def _load_market_model(loop: "LiveTradingLoop", mode: str, config_path: str = "config.json") -> None:
    """Load the appropriate model for the current market mode.

    Resolution chain (first success wins):
        1. config.json -> model.markets[mode]
        2. Built-in default (HK -> v3_hk_stocks, US/IDLE -> v3_us_stocks)
        3. Sibling market's model (HK falls through to US, and vice versa)
        4. Registry default in models_registry.json

    Steps 1–3 produce a logical name; the ModelManager / ModelRegistry then
    resolves that to an actual checkpoint file via aliases. This means the
    user can swap models by editing config.json or models_registry.json
    without touching code.
    """
    primary = _model_name_for_market(mode, config_path)
    fallback_mode = "US" if mode == "HK" else "HK" if mode == "US" else None
    fallback = _model_name_for_market(fallback_mode, config_path) if fallback_mode else None

    candidates = [primary] + ([fallback] if fallback and fallback != primary else [])

    last_exc: Optional[Exception] = None
    for name in candidates:
        try:
            loop.logger.info("Switching model for market %s: %s", mode, name)
            loop.model_manager.load(name)
            if name != primary:
                loop.logger.info("Loaded fallback model %s for market %s", name, mode)
            return
        except Exception as exc:
            last_exc = exc
            loop.logger.warning("Failed to load model %s for market %s: %s", name, mode, exc)

    loop.logger.error(
        "No usable model for market %s after trying %s; continuing without weights. Last error: %s",
        mode, candidates, last_exc,
    )


def _check_opend_reachable(host: str = "127.0.0.1", port: int = 11112, timeout: float = 2.0) -> bool:
    """Return True if OpenD TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_opend(
    host: str = "127.0.0.1",
    port: int = 11112,
    max_wait_seconds: float = 60.0,
    poll_interval_seconds: float = 5.0,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Block until OpenD is reachable or max_wait_seconds elapsed.

    Returns True if OpenD became reachable, False if the wait timed out.
    Structured log event: ``opend_guard`` with ``status``, ``host``, ``port``,
    ``elapsed_s``.
    """
    _log = logger or logging.getLogger("opend_guard")
    deadline = time.monotonic() + max_wait_seconds
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        if _check_opend_reachable(host=host, port=port):
            elapsed = max_wait_seconds - (deadline - time.monotonic())
            _log.info(
                '{"event":"opend_guard","status":"reachable","host":"%s","port":%d,"attempt":%d,"elapsed_s":%.1f}',
                host, port, attempt, elapsed,
            )
            return True
        remaining = deadline - time.monotonic()
        _log.warning(
            '{"event":"opend_guard","status":"waiting","host":"%s","port":%d,"attempt":%d,"remaining_s":%.1f}',
            host, port, attempt, max(0.0, remaining),
        )
        time.sleep(min(poll_interval_seconds, max(0.0, remaining)))

    _log.error(
        '{"event":"opend_guard","status":"timeout","host":"%s","port":%d,"max_wait_s":%.1f}',
        host, port, max_wait_seconds,
    )
    return False


def _safe_connector_connect(
    connector,
    symbols: list[str] | None = None,
    timeout_seconds: float = 90.0,
    logger: logging.Logger | None = None,
) -> bool:
    """Connect to Futu connector with a wall-clock timeout.

    The Futu SDK's internal ``_connect_sync`` loop can hang for 20+ minutes when
    port 11112 is TCP-reachable but the protocol handshake keeps timing out.
    This guard runs the blocking connect in a daemon thread and abandons it after
    ``timeout_seconds``, letting the caller proceed in degraded (no-Futu) mode.

    Returns True on success, False if the connection timed out.
    Raises RuntimeError if the connector itself raises an exception before timeout.

    Config key: ``futu_connect_timeout_seconds`` (default 90).
    """
    _log = logger or logging.getLogger("futu_connect_guard")
    error: list[Exception | None] = [None]

    def _do_connect() -> None:
        try:
            if symbols is not None:
                connector.connect(symbols=symbols)
            else:
                connector.connect()
        except TypeError:
            try:
                connector.connect()
            except Exception as exc:
                error[0] = exc
        except Exception as exc:
            error[0] = exc

    t = threading.Thread(target=_do_connect, daemon=True, name="futu_connect")
    t.start()
    t.join(timeout=timeout_seconds)

    if t.is_alive():
        # SDK connect still running — proceed in degraded mode; daemon thread
        # will be cleaned up on process exit.
        _log.error(
            '{"event":"futu_connect_timeout","timeout_s":%.1f,'
            '"msg":"Futu SDK connect hung; proceeding in degraded mode"}',
            timeout_seconds,
        )
        return False

    if error[0] is not None:
        raise RuntimeError(f"Failed to connect to Futu OpenD: {error[0]}") from error[0]

    return True


async def _apply_market_mode(loop: "LiveTradingLoop", base_cfg: "LiveConfig", mode: str, prev_mode: str | None, config_path: str = "config.json") -> None:
    symbols = _symbols_for_mode(mode)
    auto_trade = mode in {"HK", "US"} and base_cfg.auto_trade
    market_prefix = "HK" if mode == "HK" else "US"

    loop.config = replace(base_cfg, symbols_list=symbols, auto_trade=auto_trade)
    loop.symbols = symbols
    if hasattr(loop.futu_connector, "set_market_prefix"):
        loop.futu_connector.set_market_prefix(market_prefix)
    
    # New: Ensure we are subscribed to the current symbol list for real-time quotes
    sub_targets = symbols if mode != "IDLE" else IDLE_COLLECTION_SYMBOLS
    if hasattr(loop.futu_connector, "subscribe_symbols"):
        loop.futu_connector.subscribe_symbols(sub_targets)

    for symbol in symbols:
        _ensure_symbol_state(loop, symbol)

    if prev_mode != mode:
        _load_market_model(loop, mode, config_path)

    if prev_mode is None:
        if mode == "IDLE":
            loop.logger.info("[MARKET_INIT:IDLE] idle_symbols=%d auto_trade=%s", len(IDLE_COLLECTION_SYMBOLS), auto_trade)
        else:
            loop.logger.info("[MARKET_INIT:%s] symbols=%s auto_trade=%s", mode, symbols, auto_trade)
    elif prev_mode != mode:
        if mode == "IDLE":
            loop.logger.info("[MARKET_SWITCH: %s→IDLE] idle_symbols=%d auto_trade=%s", prev_mode, len(IDLE_COLLECTION_SYMBOLS), auto_trade)
        else:
            loop.logger.info("[MARKET_SWITCH: %s→%s] symbols=%s auto_trade=%s", prev_mode, mode, symbols, auto_trade)

    # 2026-04-15 Fix: Ensure ALL collection symbols have buffers, even in IDLE mode
    init_targets = symbols if mode != "IDLE" else IDLE_COLLECTION_SYMBOLS
    for symbol in init_targets:
        _ensure_symbol_state(loop, symbol)

    if mode == "IDLE":
        loop.logger.info("[MARKET_IDLE] Running market analysis...")
        try:
            analyzer = MarketAnalyzer(loop)
            report, analysis = await analyzer.generate_market_report()
            risk_mode, risk_mult = analyzer.compute_risk_factor(analysis)

            # Send Telegram report
            await loop.telegram.send_message(report)

            # Update loop risk attributes
            loop.risk_mode = risk_mode
            loop.risk_multiplier = risk_mult
            loop.logger.info(f"[MARKET_IDLE] Risk updated: {risk_mode} (x{risk_mult})")
        except Exception as e:
            loop.logger.warning(f"[MARKET_IDLE] Macro analysis failed: {e}")

        loop.logger.info(
            "[MARKET_IDLE] Outside HK/US trading hours; collect_only=%d auto_trade=%s",
            len(IDLE_COLLECTION_SYMBOLS),
            auto_trade,
        )

        # Start IdleTaskScheduler in the background for off-hours pre-computation.
        # Singleton protection inside IdleTaskScheduler prevents double-launch
        # even if prev_mode == mode (e.g., repeated IDLE cycles).
        if prev_mode != mode:
            try:
                from idle_task_scheduler import IdleSchedulerConfig, IdleTaskScheduler
                idle_cfg = IdleSchedulerConfig.from_config_json(config_path)
                scheduler = IdleTaskScheduler(
                    loop,
                    cfg=idle_cfg,
                    config_path=config_path,
                    idle_symbols=IDLE_COLLECTION_SYMBOLS,
                )
                asyncio.create_task(scheduler.run(), name="idle_task_scheduler")
                loop.logger.info("[MARKET_IDLE] IdleTaskScheduler launched (budget=%.1fh)", idle_cfg.max_idle_hours_per_day)
            except Exception as exc:
                loop.logger.warning("[MARKET_IDLE] IdleTaskScheduler failed to start: %s", exc)


async def _collect_only_cycle(loop: "LiveTradingLoop", symbols: list[str]) -> None:
    """During IDLE: only fetch latest bars to keep buffers warm, no trades."""
    loop.logger.info("[IDLE_COLLECT] Syncing %d symbols...", len(symbols))
    fetched = 0
    timed_out = 0
    errors = 0
    for i in range(0, len(symbols), 5):
        batch = symbols[i : i + 5]
        for sym in batch:
            try:
                quote = await asyncio.wait_for(
                    asyncio.to_thread(loop.futu_connector.get_latest_quote, sym),
                    timeout=10.0,
                )
                if quote:
                    new_row = pd.DataFrame([quote])
                    # Drop rows that are entirely NaN to avoid FutureWarning from
                    # pd.concat when concatenating empty/all-NA frames.
                    new_row = new_row.dropna(how="all")
                    if not new_row.empty:
                        existing = loop.market_buffers.get(sym, pd.DataFrame())
                        if existing.empty:
                            loop.market_buffers[sym] = loop._normalize_market_buffer(new_row)
                        else:
                            loop.market_buffers[sym] = loop._normalize_market_buffer(
                                pd.concat([existing, new_row], ignore_index=True)
                            )
                        fetched += 1
            except asyncio.TimeoutError:
                timed_out += 1
                loop.logger.debug("[IDLE_COLLECT] %s quote timeout, skipping symbol", sym)
            except Exception as exc:
                errors += 1
                loop.logger.debug("[IDLE_COLLECT] %s quote error: %s", sym, exc)
        # Sleep between batches to be gentle with the API
        await asyncio.sleep(2.0)
    if timed_out or errors:
        loop.logger.warning(
            "[IDLE_COLLECT] Completed: fetched=%d timed_out=%d errors=%d / %d symbols",
            fetched, timed_out, errors, len(symbols),
        )


def _idle_precompute(loop: "LiveTradingLoop", symbols: list[str]) -> None:
    """During HK session: pre-compute predictions for liquid US stocks."""
    loop.logger.info("[IDLE_PRECOMPUTE] Scoring %d US candidates...", len(symbols))
    for sym in symbols:
        try:
            df = loop.market_buffers.get(sym)
            if df is None or len(df) < 60:
                continue
            
            # 2026-04-15 Fix: Must pass local data_preparer to avoid global scaling bias
            local_preparer = loop.data_preparers_by_symbol.get(sym)
            if local_preparer:
                # Predict and log results for pre-market screening
                pred = loop.model_manager.predict(df, data_preparer=local_preparer)
                loop.logger.info("[PRECOMPUTE] symbol=%s pred=%.4f", sym, pred)
            else:
                # Fallback to global if local context missing (rare)
                pred = loop.model_manager.predict(df)
                loop.logger.info("[PRECOMPUTE_GLOBAL] symbol=%s pred=%.4f", sym, pred)
        except Exception:
            pass


def _enable_dry_run_log_prefix() -> None:
    """Prefix all log records with [DRY-RUN] for this process."""
    class DryRunFilter(logging.Filter):
        def filter(self, record):
            record.msg = f"[DRY-RUN] {record.msg}"
            return True
    
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(DryRunFilter())


async def _run_market_aware(
    loop: "LiveTradingLoop",
    config_path: str = "config.json",
    *,
    once: bool = False,
    disable_trade: bool = False,
) -> None:
    prev_mode: str | None = None

    async def one_iteration() -> None:
        nonlocal prev_mode
        now = datetime.now(HK_TZ)
        # [V3-Dynamic-Screener] Daily Funnel at 08:00 HKT
        today_str = now.strftime('%Y-%m-%d')
        last_screen = getattr(loop, '_last_funnel_date', '')
        if now.hour == 8 and now.minute < 15 and today_str != last_screen:
            print('[launcher] TRIGGER: Daily Stock Screening Funnel (08:00 HKT)')
            try:
                screener = V3FunnelScreener(watchlist_path='dynamic_watchlist.json')
                cfg_for_screener, _ = _read_config(config_path)
                v3_live_cfg = cfg_for_screener.get("v3_live", {})
                initial_universe = v3_live_cfg.get("screener_universe", ['AAPL', 'NVDA', 'TSLA', 'MSFT', 'GOOGL', 'AMD', 'META', 'AVGO', 'NFLX', 'TSM', 'PLTR', 'MSTR', 'COIN'])
                screener.run(initial_universe)
                setattr(loop, '_last_funnel_date', today_str)
            except Exception as e:
                err_msg = f"🚨 Screener Error: {e}"
                print(f'[launcher] {err_msg}')
                try:
                    loop._notify(err_msg)
                except Exception:
                    pass

        mode = resolve_market_mode(now)
        _write_market_config(mode, config_path)
        base_cfg = _base_live_config(config_path)
        if disable_trade:
            base_cfg = replace(base_cfg, auto_trade=False)
        await _apply_market_mode(loop, base_cfg, mode, prev_mode, config_path)

        if loop.symbols:
            if prev_mode != mode:
                primed = await loop.history_primer.prime_symbols(loop.symbols)
                for symbol, df in primed.items():
                    if not df.empty:
                        loop.market_buffers[symbol] = loop._normalize_market_buffer(df)
                loop.logger.info("History priming completed for %d symbols in %s mode", len(loop.symbols), mode)
            
            # Execute primary trading cycle (HK/US active)
            await loop.run_one_cycle()
            
            # Non-blocking background precompute for US stocks during HK session
            # 2026-04-15 Fix: Only precompute every 3 cycles to save CPU
            if mode == "HK":
                cycle_count = getattr(loop, "_precompute_cycle_count", 0)
                setattr(loop, "_precompute_cycle_count", cycle_count + 1)
                
                if cycle_count % 3 == 0:
                    # Only prime once per session to save bandwidth
                    if prev_mode != mode:
                        us_primed = await loop.history_primer.prime_symbols(TOP_PRECOMPUTE_US)
                        for sym, df in us_primed.items():
                            if not df.empty:
                                loop.market_buffers[sym] = loop._normalize_market_buffer(df)
                        loop.logger.info("[HK_BG] Primed %d US symbols for precompute", len(us_primed))
                    
                    # Move synchronous precompute to a background thread to avoid blocking loop
                    asyncio.create_task(asyncio.to_thread(_idle_precompute, loop, [s for s in TOP_PRECOMPUTE_US if not s.endswith('.HK')]))
        else:
            await _collect_only_cycle(loop, IDLE_COLLECTION_SYMBOLS)

        prev_mode = mode

    if once:
        await one_iteration()
        return

    while True:
        await one_iteration()
        await asyncio.sleep(loop.config.polling_seconds)


def run_kiro_v35(*, dry_run: bool = False, once: bool = False, config_path: str = "config.json") -> None:
    from v3_pipeline.core.runtime_lock import RuntimeLock
    import logging as _logging
    _startup_log = _logging.getLogger("v3_launcher")
    lock = RuntimeLock()
    if not lock.acquire():
        existing = lock._read_existing_pid()
        _startup_log.error(
            "Another launcher instance is already running (PID %s). "
            "Refusing to start a second live trading loop.",
            existing,
        )
        return

    disable_trade = dry_run or once
    live_cfg = build_live_config(config_path)
    if disable_trade:
        live_cfg = replace(live_cfg, auto_trade=False)

    preparer = DataPreparer(lookback=60, target_col="Close")
    # Read input_dim from config, default 26 (US model feature count)
    cfg, _ = _read_config(config_path)
    model_input_dim = int(cfg.get("model", {}).get("input_dim", 26))
    model = KiroLSTM(input_dim=model_input_dim, hidden_dim=64, num_layers=2, dropout=0.2, output_dim=1)
    manager = ModelManager(model=model, data_preparer=preparer)

    loop = LiveTradingLoop(
        model_manager=manager,
        risk_controller=RiskController(),
        futu_connector=FutuConnector(),
        config=live_cfg,
    )

    now = datetime.now(HK_TZ)
    initial_mode = resolve_market_mode(now)
    _load_market_model(loop, initial_mode, config_path)

    if dry_run:
        _enable_dry_run_log_prefix()

    # Startup sequence guard: verify OpenD is reachable before connecting.
    cfg, _ = _read_config(config_path)
    _opend_host = cfg.get("futu", cfg.get("futu_api_config", {})).get("host", "127.0.0.1")
    _opend_port = int(cfg.get("futu", cfg.get("futu_api_config", {})).get("port", 11112))
    _opend_max_wait = float(cfg.get("opend_guard_max_wait_seconds", 60.0))
    _connect_timeout = float(cfg.get("futu_connect_timeout_seconds", 90.0))
    if not _wait_for_opend(host=_opend_host, port=_opend_port, max_wait_seconds=_opend_max_wait, logger=loop.logger):
        loop.logger.error(
            "OpenD not reachable at %s:%d after %.0fs — starting in degraded (collect-only) mode",
            _opend_host, _opend_port, _opend_max_wait,
        )

    if not _safe_connector_connect(loop.futu_connector, loop.config.symbols_list, timeout_seconds=_connect_timeout, logger=loop.logger):
        loop.logger.warning(
            "Futu connect timed out (%.0fs) — starting in degraded (collect-only) mode",
            _connect_timeout,
        )
    _reset_crash_count()  # Reset crash counter on clean startup
    
    # 2026-04-24 Fix: Graceful crash recovery with cooldown
    max_crashes = 5
    cooldown_seconds = 30
    
    for attempt in range(1, max_crashes + 1):
        try:
            asyncio.run(_run_market_aware(loop, config_path, once=once, disable_trade=disable_trade))
            break  # Normal exit (e.g. once=True)
        except Exception as exc:
            crash_count = _get_crash_count() + 1
            _save_crash_count(crash_count)
            
            # Check if this is a non-fatal order error
            error_str = str(exc).lower()
            is_non_fatal = any(keyword in error_str for keyword in [
                "持仓不足", "insufficient position", "position not enough",
                "order placement failed", "quantity exceeds"
            ])
            
            if is_non_fatal:
                loop.logger.warning("Non-fatal order error (attempt %d/%d): %s — cooling down %ds", 
                                    attempt, max_crashes, exc, cooldown_seconds)
            else:
                loop.logger.error("V3 crash (count=%d, attempt %d/%d): %s", 
                                  crash_count, attempt, max_crashes, exc)
            
            # Reconnect Futu contexts (non-fatal — system degrades to collect-only if Futu is offline)
            try:
                import time as _time
                loop.futu_connector.close()
                _time.sleep(cooldown_seconds)
                if not _safe_connector_connect(
                    loop.futu_connector,
                    loop.config.symbols_list,
                    timeout_seconds=_connect_timeout,
                    logger=loop.logger,
                ):
                    loop.logger.warning(
                        "Reconnect timed out (%.0fs) — continuing in degraded mode",
                        _connect_timeout,
                    )
                else:
                    loop.logger.info("Reconnected after cooldown, resuming...")
            except Exception as reconnect_err:
                loop.logger.warning(
                    "Reconnection failed: %s — continuing in degraded (collect-only) mode",
                    reconnect_err,
                )
            
            # Only escalate after max crashes
            if attempt >= max_crashes:
                try:
                    import urllib.request
                    import json as _json
                    gateway_port = os.getenv("OPENCLAW_GATEWAY_PORT", "18989")
                    gateway_host = os.getenv("OPENCLAW_GATEWAY_HOST", "127.0.0.1")
                    url = f"http://{gateway_host}:{gateway_port}/api/v1/agent/main/invoke"
                    payload = _json.dumps({
                        "message": (
                            f"🚨 V3 CRASH ALERT 🚨\n"
                            f"V3 has crashed {crash_count} times — AUTO-ESCALATING to MAIN AGENT\n"
                            f"Error: {exc}\n"
                            f"Time: {datetime.now(HK_TZ).isoformat()}"
                        )
                    }).encode()
                    req = urllib.request.Request(
                        url, data=payload, headers={"Content-Type": "application/json"}
                    )
                    urllib.request.urlopen(req, timeout=10)
                except Exception as notify_err:
                    loop.logger.warning("Main agent alert via Gateway API failed: %s", notify_err)
                    loop._notify(
                        f"🚨 V3 CRASH ALERT 🚨 (agent alert failed: {notify_err})\n"
                        f"Crash count: {crash_count} | Error: {exc}"
                    )
                raise
    
    # Cleanup on normal exit or after max attempts
    loop._archive_market_data()
    loop.futu_connector.close()
    lock.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kiro V3 live launcher")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    run_kiro_v35(dry_run=args.dry_run, once=args.once, config_path=args.config)
