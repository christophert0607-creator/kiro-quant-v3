#!/usr/bin/env python3
"""
Kiro Quant - Refactored Futu API Client

Highlights:
- Async push handlers (Ticker + TradeOrder) to reduce polling latency.
- Auto reconnect + unlock_trade flow before trading.
- Retry wrappers for transient failures (disconnect / 429 / disabled account checks).
- Multi-symbol subscribe for parallel market monitoring.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import futu as ft
import pandas as pd
import yfinance as yf

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from massive_client import MassiveClient
except ImportError:  # pragma: no cover
    class MassiveClient:
        def get_quote(self, symbol: str):
            return None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("futu_api.log"), logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("KiroFutu")


class TickerPushHandler(ft.TickerHandlerBase):
    """Official async ticker push handler."""

    def __init__(self, cache: Dict[str, Dict[str, Any]], lock: threading.Lock):
        super().__init__()
        self.cache = cache
        self.lock = lock

    def on_recv_rsp(self, rsp_pb):
        ret_code, content = super().on_recv_rsp(rsp_pb)
        if ret_code != ft.RET_OK:
            logger.warning("Ticker push parse failed: %s", content)
            return ret_code, content

        if isinstance(content, pd.DataFrame) and not content.empty:
            with self.lock:
                for _, row in content.iterrows():
                    code = str(row.get("code", ""))
                    self.cache[code] = {
                        "ticker": code.split(".")[-1] if "." in code else code,
                        "code": code,
                        "price": float(row.get("price", 0.0)),
                        "volume": float(row.get("volume", 0.0)),
                        "time": str(row.get("time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))),
                        "source": "FUTU_PUSH",
                    }
        return ft.RET_OK, content


class TradeOrderPushHandler(ft.TradeOrderHandlerBase):
    """Official async order push handler."""

    def on_recv_rsp(self, rsp_pb):
        ret_code, content = super().on_recv_rsp(rsp_pb)
        if ret_code != ft.RET_OK:
            logger.warning("Trade order push parse failed: %s", content)
            return ret_code, content

        if isinstance(content, pd.DataFrame) and not content.empty:
            row = content.iloc[0]
            logger.info(
                "Order update | order_id=%s code=%s trd_side=%s status=%s dealt_qty=%s dealt_avg_price=%s",
                row.get("order_id"),
                row.get("code"),
                row.get("trd_side"),
                row.get("order_status"),
                row.get("dealt_qty"),
                row.get("dealt_avg_price"),
            )
        return ft.RET_OK, content


class FutuTrader:
    def __init__(
        self,
        host: Optional[str] = None,
        port: int = 11111,
        pwd_unlock: str = "930607",
        market: str = "US",
        trd_env: ft.TrdEnv = ft.TrdEnv.SIMULATE,
    ):
        self.host = host or os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
        self.port = port
        self.pwd_unlock = pwd_unlock
        self.market = market
        self.trd_env = trd_env

        self.quote_ctx: Optional[ft.OpenQuoteContext] = None
        self.trd_ctx: Optional[ft.OpenUSTradeContext] = None
        self.connected = False
        self.capital_limit = 0.05
        self.massive = MassiveClient()

        self._tick_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------- lifecycle -------------------------
    def connect(self) -> bool:
        try:
            self.quote_ctx = ft.OpenQuoteContext(host=self.host, port=self.port)
            self.trd_ctx = ft.OpenUSTradeContext(host=self.host, port=self.port)

            self.quote_ctx.set_handler(TickerPushHandler(self._tick_cache, self._cache_lock))
            self.trd_ctx.set_handler(TradeOrderPushHandler())

            if not self.ensure_trade_ready(force_unlock=True):
                logger.error("Trade context not ready after connect.")
                return False

            self.connected = True
            logger.info("✅ Connected host=%s port=%s trd_env=%s", self.host, self.port, self.trd_env)
            return True
        except Exception as exc:
            logger.error("❌ Connection failed: %s", exc)
            self.disconnect()
            return False

    def disconnect(self):
        try:
            if self.quote_ctx:
                self.quote_ctx.close()
            if self.trd_ctx:
                self.trd_ctx.close()
        finally:
            self.connected = False
            logger.info("🔌 Disconnected")

    def reconnect(self) -> bool:
        logger.warning("Attempting reconnect...")
        self.disconnect()
        time.sleep(1)
        return self.connect()

    # ------------------------- resiliency -------------------------
    def _should_retry(self, err: Any) -> bool:
        msg = str(err).lower()
        retry_signals = ["429", "too many", "频率", "timeout", "disconnect", "network", "连接", "socket"]
        return any(sig in msg for sig in retry_signals)

    def _is_account_disabled(self, err: Any) -> bool:
        msg = str(err).lower()
        return "disabled" in msg or "停用" in msg or "forbidden" in msg

    def _retry_api(self, fn, retries: int = 3, backoff: float = 1.0):
        last_err: Optional[Exception] = None
        for i in range(1, retries + 1):
            try:
                return fn()
            except Exception as exc:  # pragma: no cover - runtime behavior
                last_err = exc
                if self._is_account_disabled(exc):
                    logger.error("Account disabled / forbidden: %s", exc)
                    raise
                if not self._should_retry(exc) or i == retries:
                    raise
                sleep_s = backoff * i
                logger.warning("API retry %d/%d after %.1fs due to: %s", i, retries, sleep_s, exc)
                time.sleep(sleep_s)
                self.ensure_trade_ready(force_unlock=False)
        if last_err:
            raise last_err

    def ensure_trade_ready(self, force_unlock: bool = False) -> bool:
        if self.trd_ctx is None:
            return False

        def _unlock_once() -> Tuple[int, Any]:
            return self.trd_ctx.unlock_trade(self.pwd_unlock)

        try:
            if force_unlock:
                ret, data = _unlock_once()
                if ret != ft.RET_OK:
                    raise RuntimeError(f"unlock_trade failed: {data}")
                logger.info("🔓 unlock_trade success")
            else:
                # heartbeat check before trading
                ret, data = self.trd_ctx.accinfo_query(trd_env=self.trd_env)
                if ret != ft.RET_OK:
                    ret2, data2 = _unlock_once()
                    if ret2 != ft.RET_OK:
                        raise RuntimeError(f"accinfo_query failed: {data}; unlock fallback failed: {data2}")
                    logger.info("🔓 unlock_trade fallback success")
            return True
        except Exception as exc:
            logger.warning("ensure_trade_ready failed: %s", exc)
            return self.reconnect()

    # ------------------------- market data -------------------------
    def subscribe_symbols(self, symbols: List[str]) -> bool:
        if not self.quote_ctx:
            raise RuntimeError("Quote context not initialized")

        codes = [f"{self.market}.{s}" for s in symbols]

        def _op():
            ret, data = self.quote_ctx.subscribe(codes, [ft.SubType.TICKER], subscribe_push=True)
            if ret != ft.RET_OK:
                raise RuntimeError(data)
            logger.info("✅ Subscribed ticker push for symbols=%s", symbols)
            return True

        return bool(self._retry_api(_op))

    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        code = f"{self.market}.{symbol}"

        with self._cache_lock:
            cached = self._tick_cache.get(code)
        if cached:
            return dict(cached)

        data = {"ticker": symbol, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        # fallback 1: quote polling (only when push cache not available)
        try:
            if self.quote_ctx is not None:
                ret, futu_df = self.quote_ctx.get_stock_quote([code])
                if ret == ft.RET_OK and not futu_df.empty:
                    data["price"] = float(futu_df.iloc[0]["last_price"])
                    data["source"] = "FUTU_QUOTE"
                    return data
        except Exception as exc:
            logger.warning("Quote fallback failed: %s", exc)

        # fallback 2: Massive
        try:
            massive_data = self.massive.get_quote(symbol)
            if massive_data and "price" in massive_data:
                data["price"] = float(massive_data["price"])
                data["source"] = "MASSIVE"
                return data
        except Exception as exc:
            logger.warning("Massive fallback failed: %s", exc)

        # fallback 3: yfinance
        try:
            hist = yf.Ticker(symbol).history(period="1d")
            if not hist.empty:
                data["price"] = float(hist["Close"].iloc[-1])
                data["source"] = "YFINANCE"
                return data
        except Exception as exc:
            logger.warning("yfinance fallback failed: %s", exc)

        data["price"] = 0.0
        data["source"] = "FAILED"
        return data

    # ------------------------- account/risk/trade -------------------------
    def get_assets(self) -> Optional[Dict[str, Any]]:
        if not self.trd_ctx:
            return None

        def _op():
            ret, df = self.trd_ctx.accinfo_query(trd_env=self.trd_env)
            if ret != ft.RET_OK:
                raise RuntimeError(df)
            if df is None or df.empty:
                return None
            return df.iloc[0].to_dict()

        try:
            return self._retry_api(_op)
        except Exception as exc:
            logger.error("get_assets failed: %s", exc)
            return None

    def check_risk(self, qty: int, price: float) -> bool:
        assets = self.get_assets()
        if not assets:
            return False
        order_val = qty * price
        total_val = float(assets.get("total_assets", 0))
        if total_val <= 0:
            return False
        return order_val <= (total_val * self.capital_limit)

    def place_market_order(self, symbol: str, qty: int, side: ft.TrdSide) -> Optional[pd.DataFrame]:
        if not self.trd_ctx:
            return None
        if qty <= 0:
            logger.warning("Skip order: invalid qty=%s", qty)
            return None

        if not self.ensure_trade_ready(force_unlock=False):
            logger.error("Trade context unavailable, cannot place order")
            return None

        code = f"{self.market}.{symbol}"

        def _op():
            ret, data = self.trd_ctx.place_order(
                price=0,
                qty=qty,
                code=code,
                trd_side=side,
                order_type=ft.OrderType.MARKET,
                trd_env=self.trd_env,
            )
            if ret != ft.RET_OK:
                raise RuntimeError(data)
            return data

        try:
            result = self._retry_api(_op)
            logger.info("🟢 Order submitted: %s qty=%s code=%s", side, qty, code)
            return result
        except Exception as exc:
            logger.error("place_order failed: %s", exc)
            return None

    # ------------------------- strategy loop -------------------------
    @staticmethod
    def load_config() -> Dict[str, Any]:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        try:
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as exc:
            logger.error("Config error: %s", exc)
        return {"auto_trade": False, "targets": {}}

    def execute_cycle(self, symbol: str):
        config = self.load_config()
        raw = self.get_market_data(symbol)
        current = float(raw.get("price", 0.0))

        target = config.get(symbol, {}) or config.get("targets", {}).get(symbol, {})
        buy_price = float(target.get("buy_price", 0) or 0)
        sell_price = float(target.get("sell_price", 0) or 0)

        action = "HOLD"
        reason = "No trigger"

        if current > 0:
            if buy_price > 0 and current <= buy_price:
                action = "BUY"
                reason = f"price<=buy_target ({current:.4f}<={buy_price:.4f})"
            elif sell_price > 0 and current >= sell_price:
                action = "SELL"
                reason = f"price>=sell_target ({current:.4f}>={sell_price:.4f})"

        if action in {"BUY", "SELL"} and config.get("auto_trade", False):
            qty = 1
            if self.check_risk(qty, current):
                side = ft.TrdSide.BUY if action == "BUY" else ft.TrdSide.SELL
                self.place_market_order(symbol, qty, side)
            else:
                reason = "Risk guard rejected order"
                action = "HOLD"

        output = {
            "cycle_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "price": current,
            "source": raw.get("source", "UNKNOWN"),
            "signal": action,
            "reason": reason,
        }
        print(json.dumps(output, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="*", default=["TSLL", "TSLA", "NVDA"])
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between strategy cycles")
    args = parser.parse_args()

    trader = FutuTrader()
    if not trader.connect():
        logger.error("Startup failed: cannot connect")
        return

    try:
        trader.subscribe_symbols(args.symbols)
        logger.info("📡 Async monitoring started for symbols=%s", args.symbols)
        while True:
            for symbol in args.symbols:
                trader.execute_cycle(symbol)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        trader.disconnect()


if __name__ == "__main__":
    main()
