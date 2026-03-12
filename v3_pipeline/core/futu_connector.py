import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd


def _build_stderr_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


@dataclass
class FutuConfig:
    host: str = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port: int = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    market_prefix: str = os.getenv("FUTU_MARKET_PREFIX", "US")
    trd_env: str = os.getenv("FUTU_TRD_ENV", "SIMULATE")
    target_acc_id: Optional[int] = (
        int(os.getenv("FUTU_TARGET_ACC_ID")) if os.getenv("FUTU_TARGET_ACC_ID") else None
    )
    opend_web_port: int = int(os.getenv("FUTU_OPEND_WEB_PORT", "18889"))
    trade_password: str = os.getenv("FUTU_TRADE_PASSWORD", os.getenv("FUTU_TRADE_PWD", ""))


class FutuConnector:
    """Futu OpenAPI adapter with multi-provider quote fallback and cache discipline."""

    def __init__(self, config: Optional[FutuConfig] = None, config_json_path: str = "config.json") -> None:
        self.config = config or FutuConfig()
        self.logger = _build_stderr_logger(self.__class__.__name__)
        self.quote_ctx = None
        self.trade_ctx = None
        self.ft = None

        self.login_account: Optional[int] = None
        self.discovered_accounts: pd.DataFrame = pd.DataFrame()
        self._quote_cache: dict[str, dict[str, Any]] = {}
        self._cached_hit_count: dict[str, int] = {}

        self.yf_user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Mozilla/5.0 (X11; Linux x86_64)",
        ]
        self.yf_proxy_pool = [p.strip() for p in os.getenv("YF_PROXY_POOL", "").split(",") if p.strip()]
        self.elite_symbols = {s.strip().upper() for s in os.getenv("ELITE_SYMBOLS", "TSLA,NVDA,TQQQ").split(",") if s.strip()}

        self._load_runtime_config(config_json_path)

    def _cache_quote(self, symbol: str, quote: dict[str, Any]) -> None:
        self._quote_cache[symbol] = {"quote": quote, "ts": time.time()}

    def _get_cached_quote(self, symbol: str) -> Optional[dict[str, Any]]:
        cached = self._quote_cache.get(symbol)
        if not cached:
            return None
        self._cached_hit_count[symbol] = self._cached_hit_count.get(symbol, 0) + 1
        if symbol.upper() in self.elite_symbols and self._cached_hit_count[symbol] >= 3:
            self.logger.warning("Cache invalidated for elite symbol %s after 3 consecutive cached hits", symbol)
            self._quote_cache.pop(symbol, None)
            self._cached_hit_count[symbol] = 0
            return None
        return cached.get("quote")

    def _normalize_quote(self, close: float, open_p: float, high_p: float, low_p: float, volume: float, source: str) -> dict:
        return {
            "Date": pd.Timestamp.now(),
            "Open": float(open_p),
            "High": float(high_p),
            "Low": float(low_p),
            "Close": float(close),
            "Volume": float(volume),
            "data_source": source,
        }

    def _build_yf_session(self):
        """Build a yfinance-compatible session.

        Newer yfinance expects curl_cffi session objects; if unavailable,
        return None and let yfinance manage its own internal session.
        """
        try:
            from curl_cffi import requests as curl_requests

            session = curl_requests.Session()
            session.headers.update({"User-Agent": random.choice(self.yf_user_agents)})
            if self.yf_proxy_pool:
                proxy = random.choice(self.yf_proxy_pool)
                session.proxies.update({"http": proxy, "https": proxy})
            return session
        except Exception as exc:
            self.logger.warning("curl_cffi session unavailable for yfinance, fallback to default session: %s", exc)
            return None

    def _get_latest_quote_efinance(self, symbol: str) -> dict:
        import efinance as ef

        df = ef.stock.get_latest_quote([symbol])
        if df is None or df.empty:
            raise RuntimeError(f"efinance returned empty quote for {symbol}")
        row = df.iloc[0]
        close = float(row.get("最新价", row.get("昨收", 0.0)))
        return self._normalize_quote(
            close=close,
            open_p=float(row.get("今开", close)),
            high_p=float(row.get("最高", close)),
            low_p=float(row.get("最低", close)),
            volume=float(row.get("成交量", 0.0)),
            source="EF_LIVE",
        )

    def _get_latest_quote_yfinance(self, symbol: str) -> dict:
        import yfinance as yf

        session = self._build_yf_session()
        if session is None:
            ticker = yf.Ticker(symbol)
        else:
            ticker = yf.Ticker(symbol, session=session)
        hist = ticker.history(period="1d", interval="1m")
        if hist.empty:
            raise RuntimeError(f"yfinance returned empty history for {symbol}")

        row = hist.iloc[-1]
        close = float(row.get("Close", 0.0))
        return self._normalize_quote(
            close=close,
            open_p=float(row.get("Open", close)),
            high_p=float(row.get("High", close)),
            low_p=float(row.get("Low", close)),
            volume=float(row.get("Volume", 0.0)),
            source="YF_LIVE",
        )

    def _load_runtime_config(self, config_json_path: str) -> None:
        try:
            p = Path(config_json_path)
            if not p.exists():
                return
            data = json.loads(p.read_text(encoding="utf-8"))
            futu_section = data.get("futu", {}) if isinstance(data, dict) else {}
            target = futu_section.get("target_acc_id")
            if target is not None and self.config.target_acc_id is None:
                self.config.target_acc_id = int(target)
                self.logger.info("Loaded target_acc_id from config.json: %s", self.config.target_acc_id)
        except Exception as exc:
            self.logger.warning("Failed to parse config.json for futu settings: %s", exc)

    def connect(self) -> None:
        try:
            import futu as ft

            self.ft = ft
            self.quote_ctx = ft.OpenQuoteContext(host=self.config.host, port=self.config.port)
            self.trade_ctx = ft.OpenUSTradeContext(host=self.config.host, port=self.config.port)
            self.logger.info("Connected to Futu OpenD at %s:%s", self.config.host, self.config.port)
            self.discover_accounts()
            if self.config.trd_env.upper() == "REAL":
                self.unlock_trading(self.config.trade_password)
        except Exception as exc:
            self._safe_close_contexts()
            raise RuntimeError(f"Failed to connect to Futu OpenD: {exc}") from exc

    def close(self) -> None:
        self._safe_close_contexts()
        self.logger.info("Closed Futu contexts")

    def _safe_close_contexts(self) -> None:
        for ctx in [self.quote_ctx, self.trade_ctx]:
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass
        self.quote_ctx = None
        self.trade_ctx = None

    def _resolved_trd_env(self):
        if self.ft is None:
            raise RuntimeError("Futu SDK not loaded. Call connect() first.")
        return getattr(self.ft.TrdEnv, self.config.trd_env, self.ft.TrdEnv.SIMULATE)

    def _safe_trade_call(self, method_name: str, **kwargs):
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")
        method = getattr(self.trade_ctx, method_name)
        ret, data = method(trd_env=self._resolved_trd_env(), **kwargs)
        if ret != self.ft.RET_OK:
            raise RuntimeError(self._build_trade_error(f"{method_name} failed", data))
        return data

    def _build_trade_error(self, prefix: str, data: object) -> str:
        msg = f"{prefix}: {data}"
        lowered = str(data).lower()
        unlock_tokens = ("unlock", "未解锁", "未解鎖", "交易未解锁", "交易未解鎖")
        if any(token in lowered for token in unlock_tokens):
            msg = (
                f"{msg}. Trading may be locked; set FUTU_TRADE_PASSWORD and reconnect "
                "or call unlock_trading() before placing real orders."
            )
        return msg

    def unlock_trading(self, password: str) -> None:
        """Unlock trading capability before placing real orders."""
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")
        if self.config.trd_env.upper() != "REAL":
            self.logger.info("Skip trading unlock because trd_env=%s", self.config.trd_env)
            return
        pwd = str(password or "").strip()
        if not pwd:
            raise RuntimeError("FUTU_TRADE_PASSWORD is required when FUTU_TRD_ENV=REAL")
        ret, data = self.trade_ctx.unlock_trade(password=pwd)
        if ret != self.ft.RET_OK:
            raise RuntimeError(self._build_trade_error("Trade unlock failed", data))
        self.logger.info("Trading unlocked successfully")

    def discover_accounts(self) -> pd.DataFrame:
        try:
            data = self._safe_trade_call("get_acc_list")
        except TypeError as exc:
            if "trd_env" not in str(exc):
                self.logger.warning("Account discovery failed: %s", exc)
                self.discovered_accounts = pd.DataFrame()
                return self.discovered_accounts
            try:
                if self.trade_ctx is None or self.ft is None:
                    raise RuntimeError("FutuConnector not connected")
                ret, data = self.trade_ctx.get_acc_list(**self._account_kwargs())
                if ret != self.ft.RET_OK:
                    raise RuntimeError(self._build_trade_error("get_acc_list failed", data))
            except Exception as fallback_exc:
                self.logger.warning("Account discovery failed: %s", fallback_exc)
                self.discovered_accounts = pd.DataFrame()
                return self.discovered_accounts
        except Exception as exc:
            self.logger.warning("Account discovery failed: %s", exc)
            self.discovered_accounts = pd.DataFrame()
            return self.discovered_accounts

        self.discovered_accounts = data.copy() if data is not None else pd.DataFrame()
        if self.config.target_acc_id is None and not self.discovered_accounts.empty and "acc_id" in self.discovered_accounts.columns:
            self.config.target_acc_id = int(self.discovered_accounts.iloc[0]["acc_id"])
        return self.discovered_accounts

    def _account_kwargs(self) -> dict[str, Any]:
        if self.config.target_acc_id is None:
            return {}
        return {"acc_id": int(self.config.target_acc_id)}

    def heartbeat(self) -> bool:
        try:
            _ = self._safe_trade_call("accinfo_query", **self._account_kwargs())
            return True
        except Exception as exc:
            self.logger.warning("Futu heartbeat failed: %s", exc)
            return False

    def get_latest_quote(self, symbol: str) -> dict:
        code = f"{self.config.market_prefix}.{symbol}"
        provider_errors: list[str] = []

        for provider in ("yf", "ef", "futu"):
            try:
                if provider == "yf":
                    quote = self._get_latest_quote_yfinance(symbol)
                elif provider == "ef":
                    quote = self._get_latest_quote_efinance(symbol)
                else:
                    if self.quote_ctx is None or self.ft is None:
                        raise RuntimeError("not_connected")
                    ret, df = self.quote_ctx.get_stock_quote([code])
                    if ret != self.ft.RET_OK or df.empty:
                        raise RuntimeError(f"failed: {df}")
                    row = df.iloc[0]
                    quote = self._normalize_quote(
                        close=float(row.get("last_price", 0.0)),
                        open_p=float(row.get("open_price", row.get("last_price", 0.0))),
                        high_p=float(row.get("high_price", row.get("last_price", 0.0))),
                        low_p=float(row.get("low_price", row.get("last_price", 0.0))),
                        volume=float(row.get("volume", 0.0)),
                        source="FUTU",
                    )

                self._cache_quote(symbol, quote)
                self._cached_hit_count[symbol] = 0
                return quote
            except Exception as exc:
                provider_errors.append(f"{provider}={exc}")

        cached_quote = self._get_cached_quote(symbol)
        if cached_quote is not None:
            cached_quote = {**cached_quote, "data_source": "CACHED"}
            return cached_quote

        raise RuntimeError(f"Quote query failed for {code}. Providers exhausted: {'; '.join(provider_errors)}")

    def get_order_reference_price(self, symbol: str, side: str, fallback_price: float = 0.0) -> float:
        """Get realistic LIMIT reference price: BUY->ask, SELL->bid."""
        code = f"{self.config.market_prefix}.{symbol}"
        side_upper = side.upper()

        if self.quote_ctx is not None and self.ft is not None:
            try:
                ret, data = self.quote_ctx.get_order_book(code, num=1)
                if ret == self.ft.RET_OK and isinstance(data, dict):
                    if side_upper == "BUY":
                        asks = data.get("Ask", [])
                        if asks:
                            return float(asks[0][0])
                    else:
                        bids = data.get("Bid", [])
                        if bids:
                            return float(bids[0][0])
            except Exception as exc:
                self.logger.warning("Order book reference failed for %s: %s", code, exc)

        try:
            quote = self.get_latest_quote(symbol)
            return float(quote.get("Close", fallback_price))
        except Exception:
            return float(fallback_price)

    def get_sync_assets(self) -> dict:
        data = self._safe_trade_call("accinfo_query", **self._account_kwargs())
        if data is None or data.empty:
            raise RuntimeError("accinfo_query returned empty dataset")
        row = data.iloc[0]
        return {"total_assets": float(row.get("total_assets", 0.0)), "cash": float(row.get("cash", 0.0)), "power": float(row.get("power", 0.0))}

    def get_sync_positions(self) -> pd.DataFrame:
        data = self._safe_trade_call("position_list_query", **self._account_kwargs())
        return pd.DataFrame() if data is None else data.copy()

    def _is_account_disabled(self, row: dict[str, Any]) -> bool:
        status_keys = (
            "status",
            "trade_status",
            "acc_status",
            "account_status",
            "trd_status",
        )
        disabled_tokens = (
            "disable",
            "disabled",
            "suspend",
            "inactive",
            "停用",
            "冻结",
            "禁用",
            "不支持下单",
        )
        for key in status_keys:
            if key not in row:
                continue
            text = str(row.get(key, "")).strip().lower()
            if any(token in text for token in disabled_tokens):
                return True
        return False

    def validate_trading_ready(self, symbol: str, qty: int, side: str, est_price: float) -> None:
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")

        side_upper = side.upper()
        if qty <= 0:
            raise ValueError("qty must be > 0")

        data = self._safe_trade_call("accinfo_query", **self._account_kwargs())
        if data is None or data.empty:
            raise RuntimeError("account_precheck_failed: accinfo_query returned empty dataset")

        raw_row = data.iloc[0].to_dict()
        row = {str(k).strip().lower(): v for k, v in raw_row.items()}
        if self._is_account_disabled(row):
            raise RuntimeError("account_disabled: current account is disabled/restricted for order placement")

        if side_upper == "BUY":
            est_cost = float(max(est_price, 0.0)) * int(qty)
            cash = float(row.get("cash", row.get("available_funds", row.get("power", 0.0))) or 0.0)
            if est_cost > 0 and cash < est_cost:
                raise RuntimeError(
                    f"insufficient_cash: cash={cash:.2f} est_cost={est_cost:.2f} symbol={symbol} qty={qty}"
                )

    def place_order(self, symbol: str, qty: int, side: str, price: Optional[float] = None) -> object:
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")
        if qty <= 0:
            raise ValueError("qty must be > 0")
        code = f"{self.config.market_prefix}.{symbol}"
        limit_price = float(price) if price is not None else self.get_order_reference_price(symbol, side, fallback_price=0.0)
        self.validate_trading_ready(symbol=symbol, qty=qty, side=side, est_price=limit_price)
        ret, data = self.trade_ctx.place_order(
            price=limit_price,
            qty=qty,
            code=code,
            trd_side=self.ft.TrdSide.BUY if side.upper() == "BUY" else self.ft.TrdSide.SELL,
            order_type=self.ft.OrderType.NORMAL,
            trd_env=self._resolved_trd_env(),
            **self._account_kwargs(),
        )
        if ret != self.ft.RET_OK:
            raise RuntimeError(self._build_trade_error("Order placement failed", data))
        return data
