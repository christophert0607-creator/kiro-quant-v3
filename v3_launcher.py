#!/usr/bin/env python3
"""Launcher for Kiro V3 live paper auto-trading mode with market auto-switching."""

import asyncio
import json
import os
import time  # 2026-04-24: for cooldown
import argparse
import logging
import pandas as pd
from dataclasses import replace
from datetime import datetime, time as dt_time, timezone
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
# Top 30 most liquid US stocks for IDLE precompute (Optimized 2026-04-15)
TOP_PRECOMPUTE_US = [
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","AMD","AVGO","ORCL",
    "CRM","ADBE","CSCO","ACN","IBM","INTC","QCOM","TXN","AMAT","MU",
    "NFLX","COIN","MSTR","UBER","DASH","SNOW","PANW","CRWD","ZS","NET",
    "QBTS","RGTI"
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
    )


def build_live_config(config_path: str = "config.json") -> LiveConfig:
    return _base_live_config(config_path)


def _in_range(now_t: dt_time, start: dt_time, end: dt_time) -> bool:
    if start <= end:
        return start <= now_t <= end
    return now_t >= start or now_t <= end


def resolve_market_mode(now: datetime | None = None) -> str:
    now = now or datetime.now(HK_TZ)
    hk_time = now.timetz().replace(tzinfo=None)
    
    # Simple and Robust Market detection based on HK Time
    # HK Market: 09:30 - 12:00, 13:00 - 16:00
    is_hk_trading = (dt_time(9, 30) <= hk_time <= dt_time(12, 0)) or (dt_time(13, 0) <= hk_time <= dt_time(16, 0))
    if is_hk_trading:
        return "HK"
        
    # US Market (approx HKT): 21:30 - 04:00 (next day)
    is_us_trading = (hk_time >= dt_time(21, 30)) or (hk_time <= dt_time(4, 0))
    if is_us_trading:
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


def _load_market_model(loop: "LiveTradingLoop", mode: str) -> None:
    """Load the appropriate model for the current market mode."""
    model_name = "v3_us_stocks"
    if mode == "HK":
        model_name = "v3_hk_stocks"
    
    try:
        loop.logger.info("Switching model for market %s: %s", mode, model_name)
        loop.model_manager.load(model_name)
    except Exception as exc:
        loop.logger.warning("Failed to load model %s for market %s: %s", model_name, mode, exc)
        # Fallback to US model if HK missing
        if mode == "HK" and model_name != "v3_us_stocks":
            try:
                loop.model_manager.load("v3_us_stocks")
                loop.logger.info("Fallback to v3_us_stocks for HK market")
            except Exception:
                pass


def _apply_market_mode(loop: "LiveTradingLoop", base_cfg: "LiveConfig", mode: str, prev_mode: str | None) -> None:
    symbols = _symbols_for_mode(mode)
    auto_trade = mode in {"HK", "US"} and base_cfg.auto_trade
    market_prefix = "HK" if mode == "HK" else "US"

    loop.config = replace(base_cfg, symbols_list=symbols, auto_trade=auto_trade)
    loop.symbols = symbols
    loop.futu_connector.set_market_prefix(market_prefix)
    
    # New: Ensure we are subscribed to the current symbol list for real-time quotes
    sub_targets = symbols if mode != "IDLE" else IDLE_COLLECTION_SYMBOLS
    loop.futu_connector.subscribe_symbols(sub_targets)

    for symbol in symbols:
        _ensure_symbol_state(loop, symbol)

    if prev_mode != mode:
        _load_market_model(loop, mode)

    if prev_mode is None:
        loop.logger.info("[MARKET_INIT:%s] symbols=%s auto_trade=%s", mode, symbols, auto_trade)
    elif prev_mode != mode:
        loop.logger.info("[MARKET_SWITCH: %s→%s] symbols=%s auto_trade=%s", prev_mode, mode, symbols, auto_trade)

    # 2026-04-15 Fix: Ensure ALL collection symbols have buffers, even in IDLE mode
    init_targets = symbols if mode != "IDLE" else IDLE_COLLECTION_SYMBOLS
    for symbol in init_targets:
        _ensure_symbol_state(loop, symbol)

    if mode == "IDLE":
        loop.logger.info(
            "[MARKET_IDLE] Outside HK/US trading hours; collect_only=%d auto_trade=%s",
            len(IDLE_COLLECTION_SYMBOLS),
            auto_trade,
        )


async def _collect_only_cycle(loop: "LiveTradingLoop", symbols: list[str]) -> None:
    """During IDLE: only fetch latest bars to keep buffers warm, no trades."""
    loop.logger.info("[IDLE_COLLECT] Syncing %d symbols...", len(symbols))
    for i in range(0, len(symbols), 20):
        batch = symbols[i : i + 20]
        try:
            # Batch fetch to save time — use asyncio.to_thread to avoid blocking event loop
            for sym in batch:
                quote = await asyncio.to_thread(loop.futu_connector.get_latest_quote, sym)
                if quote:
                    loop.market_buffers[sym] = loop._normalize_market_buffer(
                        pd.concat([loop.market_buffers[sym], pd.DataFrame([quote])])
                    )
        except Exception as exc:
            loop.logger.warning("Idle collect batch failed: %s", exc)


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
        mode = resolve_market_mode(now)
        _write_market_config(mode, config_path)
        base_cfg = _base_live_config(config_path)
        if disable_trade:
            base_cfg = replace(base_cfg, auto_trade=False)
        _apply_market_mode(loop, base_cfg, mode, prev_mode)

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
    _load_market_model(loop, initial_mode)

    if dry_run:
        _enable_dry_run_log_prefix()

    loop.futu_connector.connect(symbols=loop.config.symbols_list)
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
            
            # Reconnect Futu contexts
            try:
                loop.futu_connector.close()
                time.sleep(cooldown_seconds)
                loop.futu_connector.connect(symbols=loop.config.symbols_list)
                loop.logger.info("Reconnected after cooldown, resuming...")
            except Exception as reconnect_err:
                loop.logger.error("Reconnection failed: %s — aborting", reconnect_err)
                raise
            
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kiro V3 live launcher")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    run_kiro_v35(dry_run=args.dry_run, once=args.once, config_path=args.config)
