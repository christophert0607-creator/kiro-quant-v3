#!/usr/bin/env python3
"""Launcher for Kiro V3 live paper auto-trading mode with market auto-switching."""

import asyncio
import json
import os
import argparse
import logging
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path
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
# Top 100 most liquid US stocks for IDLE precompute (focused, fast)
TOP_PRECOMPUTE_US = [
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","AMD","AVGO","ORCL",
    "CRM","ADBE","CSCO","ACN","IBM","INTC","QCOM","TXN","AMAT","MU",
    "NFLX","COIN","MSTR","UBER","DASH","SNOW","PANW","CRWD","ZS","NET",
    "DDOG","OKTA","MDB","PLTR","ARM","HOOD","RBLX","F","TSLL","SMCI",
    "JPM","BAC","WFC","GS","MS","V","MA","PYPL","COST",
    "LLY","UNH","JNJ","PFE","ABT","TMO","DHR","ABBV","MRK","AMGN"
]
IDLE_COLLECTION_SYMBOLS = HK_SYMBOLS + TOP_PRECOMPUTE_US  # focus on top 50 for now


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
        # yfinance primary OHLCV is 1m; polling faster than 60s tends to repeat the same candle
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
        # ---- Strategy v3: SHORT Entry gates (Plan B dual-directional) ----
        short_enabled=bool(v3_live.get("short_enabled", True)),
        rsi_overbought_entry=int(v3_live.get("rsi_overbought", 70)),
        rsi_deep_overbought=int(v3_live.get("rsi_deep_overbought", 80)),
        macd_negative_required=bool(v3_live.get("macd_negative_required", True)),
        sma_filter_short=bool(v3_live.get("sma_filter_short", True)),
        short_min_confidence_threshold=float(v3_live.get("short_min_confidence_threshold", 0.20)),
        short_sentiment_max=float(v3_live.get("short_sentiment_max", 0.10)),
        # ---- Strategy v3: SHORT Exit (Plan B) ----
        short_stop_loss=float(v3_live.get("short_stop_loss", 0.015)),
        short_take_profit=float(v3_live.get("short_take_profit", 0.02)),
        short_trailing_stop_trigger=float(v3_live.get("short_trailing_stop_trigger", 0.015)),
        short_trailing_stop_lock=float(v3_live.get("short_trailing_stop_lock", 0.0)),
    )


def build_live_config(config_path: str = "config.json") -> LiveConfig:
    return _base_live_config(config_path)


def _in_range(now_t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now_t <= end
    return now_t >= start or now_t <= end


def resolve_market_mode(now: datetime | None = None) -> str:
    now = now or datetime.now(HK_TZ)
    hk_time = now.timetz().replace(tzinfo=None)
    if _in_range(hk_time, time(9, 0), time(16, 0)):
        return "HK"
    if _in_range(hk_time, time(21, 30), time(4, 0)):
        return "US"
    return "IDLE"


def _symbols_for_mode(mode: str) -> list[str]:
    if mode == "HK":
        return HK_SYMBOLS.copy()
    if mode == "US":
        # US full universe is huge and contains many delisted tickers, which can
        # cause slow/unstable runs. Default to the smaller liquid universe unless
        # explicitly overridden.
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


def _ensure_symbol_state(loop: LiveTradingLoop, symbol: str) -> None:
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


def _apply_market_mode(loop: LiveTradingLoop, base_cfg: LiveConfig, mode: str, prev_mode: str | None) -> None:
    symbols = _symbols_for_mode(mode)
    auto_trade = mode in {"HK", "US"} and base_cfg.auto_trade
    market_prefix = "HK" if mode == "HK" else "US"

    loop.config = replace(base_cfg, symbols_list=symbols, auto_trade=auto_trade)
    loop.symbols = symbols
    loop.futu_connector.set_market_prefix(market_prefix)

    for symbol in symbols:
        _ensure_symbol_state(loop, symbol)

    if prev_mode is None:
        loop.logger.info("[MARKET_INIT:%s] symbols=%s auto_trade=%s", mode, symbols, auto_trade)
    elif prev_mode != mode:
        loop.logger.info("[MARKET_SWITCH: %s→%s] symbols=%s auto_trade=%s", prev_mode, mode, symbols, auto_trade)

    if mode == "IDLE":
        loop.logger.info(
            "[MARKET_IDLE] Outside HK/US trading hours; collect_only=%d auto_trade=%s",
            len(IDLE_COLLECTION_SYMBOLS),
            auto_trade,
        )


async def _collect_only_cycle(loop: LiveTradingLoop, symbols: list[str]) -> None:
    loop._check_heartbeat()
    loop._sync_broker_state()

    async def collect(symbol: str) -> None:
        _ensure_symbol_state(loop, symbol)
        try:
            quote = await asyncio.wait_for(
                asyncio.to_thread(loop.futu_connector.get_latest_quote, symbol),
                timeout=loop.config.quote_timeout_seconds,
            )
        except TimeoutError:
            loop.logger.warning("COLLECT_TIMEOUT[%s]: quote fetch exceeded %.1fs", symbol, loop.config.quote_timeout_seconds)
            return
        except Exception as exc:
            loop.logger.warning("COLLECT_FAIL[%s]: %s", symbol, exc)
            return

        import pandas as pd

        buffer_df = pd.concat([loop.market_buffers[symbol], pd.DataFrame([quote])], ignore_index=True)
        buffer_df = loop._normalize_market_buffer(buffer_df)
        # Don't regenerate indicators on every quote (wasteful) — just ffill NaN
        buffer_df = buffer_df.ffill().bfill()
        loop.market_buffers[symbol] = buffer_df
        loop.logger.info(
            "COLLECT_ONLY[%s] size=%d source=%s",
            symbol,
            len(loop.market_buffers[symbol]),
            quote.get("data_source", "UNKNOWN"),
        )

    await asyncio.gather(*(collect(symbol) for symbol in symbols))

    # ── One-time history priming during IDLE ───────────────────────────────────
    # Fill market_buffers with enough historical bars for the screener to work
    if not getattr(loop, "_idle_primed", False):
        try:
            loop.logger.info("[IDLE_PRIMER] Priming %d symbols with history...", len(symbols))
            primed = await loop.history_primer.prime_symbols(symbols)
            for symbol, df in primed.items():
                if df is not None and not df.empty:
                    buf = loop._normalize_market_buffer(df)
                    # Generate technical indicators for screener to use
                    feat = loop.feature_generator.generate(buf)
                    buf = feat.ffill().bfill()
                    loop.market_buffers[symbol] = buf
            loop.logger.info("[IDLE_PRIMER] Primed %d symbols (with indicators)", len(primed))
            loop._idle_primed = True
        except Exception as exc:
            loop.logger.warning("[IDLE_PRIMER] Failed: %s", exc)

    # ── Idle-time pre-screening: score ALL symbols, predict top candidates ─────
    # This runs every cycle during IDLE to keep signals warm for next market open
    if os.getenv("IDLE_PRECOMPUTE", "1") == "1":
        _idle_precompute(loop, symbols)


def _idle_precompute(loop: LiveTradingLoop, symbols: list[str]) -> None:
    """During IDLE: score all symbols and pre-compute predictions for top-N candidates."""
    try:
        from v3_pipeline.features.screener import ScreenConfig, score_symbols
        cfg = ScreenConfig.from_env()

        # Score all symbols (exclude HK - lot size issues make them untradeable)
        tradeable_symbols = [s for s in symbols if not s.endswith('.HK')]
        ranked = score_symbols(loop.market_buffers, {}, {s: 0.0 for s in tradeable_symbols}, cfg)
        if not ranked:
            # Debug: check buffer columns and RSI values
            sample_buf = next((v for v in loop.market_buffers.values() if v is not None and not v.empty), None)
            if sample_buf is not None:
                cols = list(sample_buf.columns)
                rsi_col = 'RSI_14' if 'RSI_14' in cols else ('rsi_14' if 'rsi_14' in cols else 'NOT FOUND')
                _rsi_raw = sample_buf.iloc[-1].get('RSI_14', sample_buf.iloc[-1].get('rsi_14', 50.0))
                if isinstance(_rsi_raw, str) and _rsi_raw.upper() in ('N/A', 'NAN', '', 'NONE'):
                    latest_rsi = 50.0
                else:
                    try:
                        latest_rsi = float(_rsi_raw)
                    except (TypeError, ValueError):
                        latest_rsi = 50.0
                loop.logger.info("[IDLE_PRECOMPUTE] Debug: ranked=[] cols=%s rsi_col=%s rsi_val=%s", cols[:15], rsi_col, latest_rsi)
        top_candidates = [sym for sym, _ in ranked[:100]]  # pre-compute top 100

        # Pre-compute predictions for top candidates (skip if already cached recently)
        now_ts = __import__('time').time()
        cached = getattr(loop, "_idle_pred_cache", {})
        fresh = {k: v for k, v in cached.items() if now_ts - v["ts"] < 300}  # < 5 min = fresh
        stale = [s for s in top_candidates if s not in fresh]

        if stale:
            loop.logger.info("[IDLE_PRECOMPUTE] Scoring %d stale symbols (cached: %d)", len(stale), len(fresh))
            for symbol in stale[:30]:  # max 30 per cycle to avoid timeouts
                try:
                    import pandas as pd
                    buf = loop.market_buffers.get(symbol)
                    if buf is None or len(buf) < 60:
                        continue
                    lookback = int(loop.model_manager.data_preparer.lookback)
                    if len(buf) < lookback:
                        continue

                    # Buffers already have indicators (generated during priming)
                    featured = buf.ffill().bfill().dropna().reset_index(drop=True)
                    if len(featured) <= lookback:
                        continue

                    wfa_frame = loop.alpha_engine.select_features(symbol, featured)
                    preparer = loop.data_preparers_by_symbol.get(symbol)
                    if preparer is None:
                        continue

                    current_price = float(wfa_frame.iloc[-1]["Close"])
                    prediction = float(loop.model_manager.predict(wfa_frame, data_preparer=preparer))
                    confidence = min(1.0, abs(prediction - current_price) / max(current_price, 1e-9))
                    fresh[symbol] = {"pred": prediction, "conf": confidence, "ts": now_ts}
                    loop.logger.info("[IDLE_PRECOMPUTE][%s] pred=%.4f conf=%.4f", symbol, prediction, confidence)
                except Exception as exc:
                    loop.logger.debug("[IDLE_PRECOMPUTE] %s failed: %s", symbol, exc)

        loop._idle_pred_cache = fresh
        loop.logger.info("[IDLE_PRECOMPUTE] Cache size: %d symbols | top: %s", len(fresh), list(fresh.keys())[:5])
    except Exception as exc:
        loop.logger.warning("[IDLE_PRECOMPUTE] Failed: %s", exc)


def _enable_dry_run_log_prefix() -> None:
    """Prefix all log records with [DRY-RUN] for this process."""
    old_factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        record = old_factory(*args, **kwargs)
        try:
            if isinstance(record.msg, str) and not record.msg.startswith("[DRY-RUN]"):
                record.msg = "[DRY-RUN] " + record.msg
        except Exception:
            pass
        return record

    logging.setLogRecordFactory(record_factory)


async def _run_market_aware(
    loop: LiveTradingLoop,
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
            await loop.run_one_cycle()
            # Also pre-compute US signals during HK hours for warm signals at US open
            if mode == "HK":
                # Prime US stock buffers asynchronously (needed for scoring)
                us_primed = await loop.history_primer.prime_symbols(TOP_PRECOMPUTE_US)
                for sym, df in us_primed.items():
                    if not df.empty:
                        loop.market_buffers[sym] = loop._normalize_market_buffer(df)
                loop.logger.info("[HK_BG] Primed %d US symbols for precompute", len(us_primed))
                _idle_precompute(loop, [s for s in TOP_PRECOMPUTE_US if not s.endswith('.HK')])
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

    preparer = DataPreparer(lookback=60, target_col="Close")  # Must match training lookback
    model = KiroLSTM(input_dim=24, hidden_dim=64, num_layers=2, dropout=0.2, output_dim=1)
    manager = ModelManager(model=model, data_preparer=preparer)

    # Load trained model if available
    import sys as _sys
    try:
        manager.load("v3_us_stocks")
        print("Loaded trained model: v3_us_stocks", file=_sys.stderr)
    except Exception as exc:
        print(f"WARNING: No trained model found (using random weights): {exc}", file=_sys.stderr)

    loop = LiveTradingLoop(
        model_manager=manager,
        risk_controller=RiskController(),
        futu_connector=FutuConnector(),
        config=live_cfg,
    )

    if dry_run:
        _enable_dry_run_log_prefix()

    loop.futu_connector.connect()
    try:
        asyncio.run(_run_market_aware(loop, config_path, once=once, disable_trade=disable_trade))
    finally:
        loop._archive_market_data()
        loop.futu_connector.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kiro V3 live launcher")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Start engine but never place orders. Prefix logs with [DRY-RUN].",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one polling cycle (fetch→predict→decision log→NO trade) then exit 0.",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config.json (default: config.json)",
    )
    args = parser.parse_args()
    run_kiro_v35(dry_run=args.dry_run, once=args.once, config_path=args.config)
