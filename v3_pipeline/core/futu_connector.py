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

    def _get_latest_quote_alpha_vantage(self, symbol: str) -> dict:
        """Alpha Vantage via MCP server (real-time, rate-limited to 5 calls/min)."""
        try:
            from v3_pipeline.data.mcp_av_connector import get_realtime_quote as _av_quote
            q = _av_quote(symbol)
            if q is None:
                raise RuntimeError(f"MCP AV returned None for {symbol}")
            return self._normalize_quote(
                close=q["Close"],
                open_p=q["Open"],
                high_p=q["High"],
                low_p=q["Low"],
                volume=q["Volume"],
                source="AV_MCP",
            )
        except Exception as exc:
            self.logger.warning("[AV MCP] Quote error for %s: %s", symbol, exc)
            raise

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
        ticker = yf.Ticker(symbol, session=session) if session else yf.Ticker(symbol)

        # Primary: fast_info (reliable real-time price for most stocks)
        price = None
        try:
            fi = ticker.fast_info
            price = float(getattr(fi, "last_price", None) or getattr(fi, "lastPrice", None))
        except Exception:
            pass

        if price and price > 0:
            # Use DAILY bars to match training data (1d) — not 1m which corrupts daily indicators
            try:
                hist = ticker.history(period="5d", interval="1d")
                if hist is not None and not hist.empty:
                    row = hist.iloc[-1]
                    return self._normalize_quote(
                        close=float(row.get("Close", price)),
                        open_p=float(row.get("Open", price)),
                        high_p=float(row.get("High", price)),
                        low_p=float(row.get("Low", price)),
                        volume=float(row.get("Volume", 0.0)),
                        source="YF_LIVE",
                    )
            except Exception:
                pass
            # Fallback: return price-only quote (OHLCV=price, volume=0)
            now = pd.Timestamp.now()
            return self._normalize_quote(
                close=price, open_p=price, high_p=price, low_p=price,
                volume=0.0, source="YF_LIVE",
            )

        # Secondary: history-based quote
        hist = ticker.history(period="5d", interval="1d")
        if hist is None or hist.empty:
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

    def _resolve_trade_market(self, market_prefix: Optional[str] = None):
        if self.ft is None:
            raise RuntimeError("Futu SDK not loaded. Call connect() first.")
        market_name = (market_prefix or self.config.market_prefix or "US").upper()
        return getattr(self.ft.TrdMarket, market_name, self.ft.TrdMarket.US)

    def _open_trade_context(self, market_prefix: Optional[str] = None):
        if self.ft is None:
            raise RuntimeError("Futu SDK not loaded. Call connect() first.")
        trade_market = self._resolve_trade_market(market_prefix)
        return self.ft.OpenSecTradeContext(
            filter_trdmarket=trade_market,
            host=self.config.host,
            port=self.config.port,
        )

    def set_market_prefix(self, market_prefix: str) -> None:
        normalized = (market_prefix or "US").upper()
        old_prefix = (self.config.market_prefix or "US").upper()
        self.config.market_prefix = normalized

        if self.ft is None or self.quote_ctx is None:
            return
        if normalized == old_prefix and self.trade_ctx is not None:
            return

        old_trade_ctx = self.trade_ctx
        try:
            self.trade_ctx = self._open_trade_context(normalized)
            if old_trade_ctx is not None:
                try:
                    old_trade_ctx.close()
                except Exception:
                    pass
            self.logger.info("Switched trade context: %s→%s", old_prefix, normalized)
            self.discover_accounts()
        except Exception:
            self.trade_ctx = old_trade_ctx
            raise

    def connect(self) -> None:
        # Modes:
        # - NO_FUTU=1: skip all futu (default YF-only)
        # - FUTU_TRADE_ONLY=1: connect trade/positions only, keep quotes on YF/efinance
        no_futu = os.getenv("NO_FUTU", "").strip() == "1"
        trade_only = os.getenv("FUTU_TRADE_ONLY", "").strip() == "1"

        if no_futu and not trade_only:
            self.logger.info("NO_FUTU=1 — skipping Futu connection, YF-only mode")
            self.ft = None
            self.quote_ctx = None
            self.trade_ctx = None
            return
        try:
            import futu as ft

            self.ft = ft

            # Safety: block REAL trading unless explicitly allowed.
            if str(self.config.trd_env).upper() == "REAL" and os.getenv("ALLOW_REAL_TRADING", "").strip() != "1":
                raise RuntimeError("REAL trading blocked. Set ALLOW_REAL_TRADING=1 to enable.")

            self.quote_ctx = None if trade_only else ft.OpenQuoteContext(host=self.config.host, port=self.config.port)
            self.trade_ctx = self._open_trade_context(self.config.market_prefix)
            self.logger.info(
                "Connected to Futu OpenD at %s:%s (trade_market=%s, trade_only=%s)",
                self.config.host,
                self.config.port,
                self.config.market_prefix,
                trade_only,
            )
            self.discover_accounts()
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
        """Call trade API safely across futu-api versions.

        Some endpoints (e.g. get_acc_list) do not accept trd_env in newer SDKs.
        We try with trd_env first, and fall back to calling without it.
        """
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")
        method = getattr(self.trade_ctx, method_name)
        try:
            ret, data = method(trd_env=self._resolved_trd_env(), **kwargs)
        except TypeError:
            ret, data = method(**kwargs)
        if ret != self.ft.RET_OK:
            raise RuntimeError(f"{method_name} failed: {data}")
        return data

    def discover_accounts(self) -> pd.DataFrame:
        try:
            data = self._safe_trade_call("get_acc_list")
            self.discovered_accounts = data.copy() if data is not None else pd.DataFrame()
            if self.config.target_acc_id is None and not self.discovered_accounts.empty and "acc_id" in self.discovered_accounts.columns:
                # Prefer first ACTIVE account over DISABLED
                active = self.discovered_accounts[self.discovered_accounts["acc_status"] == "ACTIVE"]
                if not active.empty:
                    self.config.target_acc_id = int(active.iloc[0]["acc_id"])
                    self.logger.info("Selected active account: %s (trd_env=%s)", self.config.target_acc_id, active.iloc[0]["trd_env"])
                else:
                    self.config.target_acc_id = int(self.discovered_accounts.iloc[0]["acc_id"])
                    self.logger.warning("All accounts DISABLED, using first: %s", self.config.target_acc_id)
            return self.discovered_accounts
        except Exception as exc:
            self.logger.warning("Account discovery failed: %s", exc)
            self.discovered_accounts = pd.DataFrame()
            return self.discovered_accounts

    def _account_kwargs(self) -> dict[str, Any]:
        # Note: accinfo_query and position_list_query do NOT accept acc_id param
        # — the account is bound to the connection context at open time.
        # Passing acc_id explicitly causes "Nonexisting acc_id" errors.
        # Always return empty dict; context default account is used.
        return {}

    def heartbeat(self) -> bool:
        if self.trade_ctx is None or self.ft is None:
            # NO_FUTU mode: skip futu heartbeat, rely on YF data
            return True
        try:
            _ = self._safe_trade_call("accinfo_query", **self._account_kwargs())
            return True
        except Exception as exc:
            self.logger.warning("Futu heartbeat failed: %s", exc)
            return False

    def resolve_symbol(self, symbol: str) -> tuple[str, str]:
        raw = str(symbol).strip()
        upper = raw.upper()

        if upper.endswith(".HK"):
            market_prefix = "HK"
            base_symbol = upper[:-3]
            provider_symbol = f"{base_symbol}.HK"
        elif "." in upper:
            prefix, rest = upper.split(".", 1)
            market_prefix = prefix or self.config.market_prefix
            base_symbol = rest
            provider_symbol = rest
            if market_prefix == "HK" and not provider_symbol.endswith(".HK"):
                provider_symbol = f"{base_symbol}.HK"
        else:
            market_prefix = self.config.market_prefix
            base_symbol = upper
            provider_symbol = f"{base_symbol}.HK" if market_prefix == "HK" else base_symbol

        base_symbol_futu = base_symbol
        if market_prefix == "HK" and base_symbol.isdigit() and len(base_symbol) < 5:
            base_symbol_futu = base_symbol.zfill(5)

        broker_code = f"{market_prefix}.{base_symbol_futu}"
        return broker_code, provider_symbol

    def get_latest_quote(self, symbol: str) -> dict:
        code, provider_symbol = self.resolve_symbol(symbol)
        provider_errors: list[str] = []

        # Retry logic with exponential backoff
        max_retries = 3
        # Use Alpha Vantage (av) as primary for Elite US symbols due to better realtime accuracy.
        # But respect the 5 calls/min rate limit by only using it for Elite list.
        is_elite = symbol.upper() in self.elite_symbols and not symbol.endswith(".HK")
        
        if self.quote_ctx is None or self.ft is None:
            # NO_FUTU mode: AV -> YF -> EF
            providers = ("av", "yf", "ef") if is_elite else ("yf", "ef")
        else:
            # Full mode: AV -> YF -> EF -> FUTU
            providers = ("av", "yf", "ef", "futu") if is_elite else ("yf", "ef", "futu")

        for attempt in range(max_retries):
            for provider in providers:
                try:
                    if provider == "av":
                        quote = self._get_latest_quote_alpha_vantage(provider_symbol)
                    elif provider == "yf":
                        quote = self._get_latest_quote_yfinance(provider_symbol)
                    elif provider == "ef":
                        quote = self._get_latest_quote_efinance(provider_symbol)
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
                    provider_errors.append(f"{provider}(att:{attempt})={exc}")
            
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                self.logger.warning(f"Retrying quote fetch for {symbol} in {wait_time}s...")
                time.sleep(wait_time)

        cached_quote = self._get_cached_quote(symbol)
        if cached_quote is not None:
            cached_quote = {**cached_quote, "data_source": "CACHED"}
            return cached_quote

        raise RuntimeError(f"Quote query failed for {code}. Providers exhausted: {'; '.join(provider_errors)}")

    def get_order_reference_price(self, symbol: str, side: str, fallback_price: float = 0.0) -> float:
        """Get realistic LIMIT reference price: BUY->ask, SELL->bid."""
        code, _ = self.resolve_symbol(symbol)
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
        if self.trade_ctx is None or self.ft is None:
            # NO_FUTU mode: return simulated paper trading assets
            return {"total_assets": 100000.0, "cash": 100000.0, "power": 100000.0}
        data = self._safe_trade_call("accinfo_query", **self._account_kwargs())
        if data is None or data.empty:
            raise RuntimeError("accinfo_query returned empty dataset")
        row = data.iloc[0]
        return {"total_assets": float(row.get("total_assets", 0.0)), "cash": float(row.get("cash", 0.0)), "power": float(row.get("power", 0.0))}

    def get_sync_positions(self) -> pd.DataFrame:
        if self.trade_ctx is None or self.ft is None:
            # NO_FUTU mode: return empty — positions tracked by state.json
            return pd.DataFrame()
        try:
            data = self._safe_trade_call("position_list_query", **self._account_kwargs())
            if data is None:
                return pd.DataFrame()
            return data
        except Exception as exc:
            self.logger.warning("Position sync failed (fallback to default acc): %s", exc)
            try:
                data = self._safe_trade_call("position_list_query")
                return data if data is not None else pd.DataFrame()
            except Exception:
                return pd.DataFrame()

    def place_order(self, symbol: str, qty: int, side: str, price: Optional[float] = None) -> object:
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")
        qty = int(round(qty))  # Ensure integer qty
        if qty <= 0:
            self.logger.info("place_order skipped: qty=%d for %s", qty, symbol)
            return None
        # HK stocks: enforce minimum 1 full lot (100 shares), skip if less
        if symbol.endswith(".HK") and qty < 100:
            self.logger.info("place_order skipped: HK %s qty=%d < 1 lot (100)", symbol, qty)
            return None
        code, _ = self.resolve_symbol(symbol)
        limit_price = float(price) if price is not None else self.get_order_reference_price(symbol, side, fallback_price=0.0)
        limit_price = round(limit_price, 2)
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
            raise RuntimeError(f"Order placement failed: {data}")
        return data
