#!/usr/bin/env python3
"""Launcher for Kiro V3 live paper auto-trading mode with market auto-switching."""

import asyncio
import json
import os
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
US_SYMBOLS = ["NVDA", "TSLA", "F", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "AMD", "PLTR", "OKLO", "URA", "TSLL"]
IDLE_COLLECTION_SYMBOLS = HK_SYMBOLS + US_SYMBOLS


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
        max_portfolio_positions=int(v3_live.get("max_portfolio_positions", 8)),
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
        loop.market_buffers[symbol] = loop._normalize_market_buffer(buffer_df)
        loop.logger.info(
            "COLLECT_ONLY[%s] size=%d source=%s",
            symbol,
            len(loop.market_buffers[symbol]),
            quote.get("data_source", "UNKNOWN"),
        )

    await asyncio.gather(*(collect(symbol) for symbol in symbols))


async def _run_market_aware(loop: LiveTradingLoop, config_path: str = "config.json") -> None:
    prev_mode: str | None = None

    while True:
        now = datetime.now(HK_TZ)
        mode = resolve_market_mode(now)
        _write_market_config(mode, config_path)
        base_cfg = _base_live_config(config_path)
        _apply_market_mode(loop, base_cfg, mode, prev_mode)

        if loop.symbols:
            if prev_mode != mode:
                primed = await loop.history_primer.prime_symbols(loop.symbols)
                for symbol, df in primed.items():
                    if not df.empty:
                        loop.market_buffers[symbol] = loop._normalize_market_buffer(df)
                loop.logger.info("History priming completed for %d symbols in %s mode", len(loop.symbols), mode)
            await loop.run_one_cycle()
        else:
            await _collect_only_cycle(loop, IDLE_COLLECTION_SYMBOLS)

        prev_mode = mode
        await asyncio.sleep(loop.config.polling_seconds)


def run_kiro_v35() -> None:
    live_cfg = build_live_config()

    preparer = DataPreparer(lookback=40, target_col="Close")
    model = KiroLSTM(input_dim=24, hidden_dim=64, num_layers=2, dropout=0.2, output_dim=1)
    manager = ModelManager(model=model, data_preparer=preparer)

    loop = LiveTradingLoop(
        model_manager=manager,
        risk_controller=RiskController(),
        futu_connector=FutuConnector(),
        config=live_cfg,
    )

    loop.futu_connector.connect()
    try:
        asyncio.run(_run_market_aware(loop))
    finally:
        loop._archive_market_data()
        loop.futu_connector.close()


if __name__ == "__main__":
    run_kiro_v35()
