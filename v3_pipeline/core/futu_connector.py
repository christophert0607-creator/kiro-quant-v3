import asyncio
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd


class QuoteCache:
    """Quote cache with TTL, batch-fetch, and get_missing() support.

    Replaces the plain dict that was previously used as _quote_cache so that
    main_loop._prefetch_quotes() can call the structured methods it expects.

    Storage format (internal): symbol -> {"quote": {...}, "ts": float}
    get() / get_missing() honour a 30-second TTL by default.
    """

    TTL_SECONDS: float = 30.0

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        self._hit_count: dict[str, int] = {}

    # ── dict-compatible interface (used by _cache_quote / _get_cached_quote) ──

    def __setitem__(self, symbol: str, value: Any) -> None:
        """Store a quote.  Accepts both raw-quote dicts and {"quote":…,"ts":…} wrappers."""
        if isinstance(value, dict) and "quote" in value and "ts" in value:
            self._data[symbol] = value
        else:
            self._data[symbol] = {"quote": value, "ts": time.time()}

    def __contains__(self, symbol: object) -> bool:
        return symbol in self._data

    def pop(self, symbol: str, *args: Any) -> Any:
        return self._data.pop(symbol, *args)

    def get(self, symbol: str, default: Any = None) -> Any:
        """Return the inner quote dict (with _cached_at) if fresh, else default.

        main_loop._run_symbol_cycle expects quote.get("Close"), quote.get("_cached_at"), …
        """
        entry = self._data.get(symbol)
        if not entry:
            return default
        age = time.time() - entry.get("ts", 0)
        if age > self.TTL_SECONDS:
            return default
        inner: dict = dict(entry.get("quote") or entry)
        inner.setdefault("_cached_at", entry.get("ts", 0))
        return inner

    # ── structured interface used by main_loop._prefetch_quotes ───────────────

    def get_missing(self, symbols: List[str]) -> List[str]:
        """Return symbols whose cache entry is absent or older than TTL_SECONDS."""
        now = time.time()
        return [
            s for s in symbols
            if s not in self._data or (now - self._data[s].get("ts", 0)) > self.TTL_SECONDS
        ]

    def refresh_batch_itick(self, symbols: List[str], market: str, *args: Any) -> dict:
        """Stub: iTick batch fetch not wired up; returns empty dict."""
        return {}

    async def refresh_batch_async(
        self,
        symbols: List[str],
        fetcher: Any,
        max_concurrency: int = 8,
    ) -> dict:
        """Fetch quotes for ``symbols`` concurrently via ``fetcher(symbol)`` (sync callable).

        Populates cache and returns a mapping of symbol -> quote for successful fetches.
        """
        results: dict = {}
        sem = asyncio.Semaphore(max_concurrency)

        async def _fetch(sym: str) -> None:
            async with sem:
                try:
                    quote = await asyncio.to_thread(fetcher, sym)
                    if quote:
                        self[sym] = {"quote": quote, "ts": time.time()}
                        results[sym] = quote
                except Exception:
                    pass

        await asyncio.gather(*(_fetch(s) for s in symbols))
        return results

    def log_stats(self) -> None:
        total = len(self._data)
        now = time.time()
        fresh = sum(1 for v in self._data.values() if (now - v.get("ts", 0)) <= self.TTL_SECONDS)
        logger = logging.getLogger("QuoteCache")
        logger.debug("[CACHE_STATS] total=%d fresh=%d stale=%d", total, fresh, total - fresh)


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
        self.trade_ctx = None  # backward-compat: points to primary market context
        self.ft = None

        # Multi-market trade contexts: {"US": OpenUSTradeContext, "HK": OpenHKTradeContext}
        self.trade_ctxs: dict[str, Any] = {}
        # Per-market account IDs discovered from broker: {"US": acc_id, "HK": acc_id}
        self.account_ids: dict[str, Optional[int]] = {}

        self.login_account: Optional[int] = None
        self.discovered_accounts: pd.DataFrame = pd.DataFrame()
        self._quote_cache: QuoteCache = QuoteCache()
        self._cached_hit_count: dict[str, int] = {}

        # Degraded-mode tracking: after _FUTU_FAIL_THRESHOLD consecutive trade_ctx
        # failures, mark as degraded so callers skip Futu broker operations entirely
        # and rely on yfinance + state.json. Resets on a successful trade call.
        self._futu_fail_count: int = 0
        self._FUTU_FAIL_THRESHOLD: int = 3
        self._futu_degraded: bool = False

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
        cached = self._quote_cache.get(symbol)  # returns inner quote or None (TTL-aware)
        if not cached:
            return None
        self._cached_hit_count[symbol] = self._cached_hit_count.get(symbol, 0) + 1
        if symbol.upper() in self.elite_symbols and self._cached_hit_count[symbol] >= 3:
            self.logger.warning("Cache invalidated for elite symbol %s after 3 consecutive cached hits", symbol)
            self._quote_cache.pop(symbol, None)
            self._cached_hit_count[symbol] = 0
            return None
        return cached

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
        try:
            import efinance as ef
        except ModuleNotFoundError:
            raise RuntimeError(
                "efinance not importable in current Python env. "
                "Fix: python3 -m pip install efinance --break-system-packages  "
                "or activate the venv that has efinance installed."
            )

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
        from v3_pipeline.data.yf_provider import get_latest_quote as _yf_get
        result = _yf_get(symbol)
        if result is None:
            raise RuntimeError(f"yf_provider returned None for {symbol} (FD guard or network failure)")
        return result

    def _load_runtime_config(self, config_json_path: str) -> None:
        try:
            p = Path(config_json_path)
            if not p.exists():
                return
            data = json.loads(p.read_text(encoding="utf-8"))
            futu_section = data.get("futu", {}) if isinstance(data, dict) else {}

            # host / port — only override if NOT already set via env-var.
            env_host = os.getenv("FUTU_OPEND_HOST")
            env_port = os.getenv("FUTU_OPEND_PORT")
            cfg_host = futu_section.get("host")
            cfg_port = futu_section.get("port")
            if cfg_host and not env_host:
                self.config.host = str(cfg_host)
                self.logger.info("Loaded Futu host from config.json: %s", self.config.host)
            if cfg_port is not None and not env_port:
                self.config.port = int(cfg_port)
                self.logger.info("Loaded Futu port from config.json: %s", self.config.port)

            trd_env = futu_section.get("trd_env")
            if trd_env and not os.getenv("FUTU_TRD_ENV"):
                self.config.trd_env = str(trd_env)
                self.logger.info("Loaded Futu trd_env from config.json: %s", self.config.trd_env)

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

            # Open US trade context (always required)
            self.trade_ctxs["US"] = ft.OpenUSTradeContext(host=self.config.host, port=self.config.port)

            # Open HK trade context if the SDK supports it (optional — non-fatal if missing)
            try:
                self.trade_ctxs["HK"] = ft.OpenHKTradeContext(host=self.config.host, port=self.config.port)
            except Exception as hk_exc:
                self.logger.warning("HK trade context unavailable (non-fatal): %s", hk_exc)

            # Backward compat: trade_ctx -> primary market context
            primary = self.config.market_prefix.upper() if self.config.market_prefix.upper() in self.trade_ctxs else None
            if primary is None:
                primary = next(iter(self.trade_ctxs), None)
            self.trade_ctx = self.trade_ctxs.get(primary) if primary else None

            active_markets = list(self.trade_ctxs.keys())
            self.logger.info("Connected to Futu OpenD at %s:%s (markets: %s)", self.config.host, self.config.port, active_markets)
            self.discover_accounts()
            if self.config.trd_env.upper() == "REAL":
                self.unlock_trading(self.config.trade_password)
        except Exception as exc:
            self._safe_close_contexts()
            raise RuntimeError(f"Failed to connect to Futu OpenD: {exc}") from exc

    def get_trade_ctx(self, market: str) -> Any:
        """Return the trade context for the given market ('HK' or 'US').

        Falls back to the legacy single ``trade_ctx`` if multi-market contexts were
        not established (e.g. tests that set ``trade_ctx`` directly).
        """
        mkt = str(market).upper()
        ctx = self.trade_ctxs.get(mkt)
        if ctx is not None:
            return ctx
        if self.trade_ctx is not None:
            self.logger.debug("get_trade_ctx(%r): no per-market ctx, falling back to trade_ctx", mkt)
            return self.trade_ctx
        raise RuntimeError(f"No trade context available for market {mkt!r}")

    def close(self) -> None:
        self._safe_close_contexts()
        self.logger.info("Closed Futu contexts")

    def _safe_close_contexts(self) -> None:
        all_ctxs = [self.quote_ctx] + list(self.trade_ctxs.values())
        # Also close the legacy trade_ctx if it isn't already in trade_ctxs
        if self.trade_ctx is not None and self.trade_ctx not in self.trade_ctxs.values():
            all_ctxs.append(self.trade_ctx)
        for ctx in all_ctxs:
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass
        self.quote_ctx = None
        self.trade_ctx = None
        self.trade_ctxs = {}
        self.account_ids = {}

    @property
    def is_connected(self) -> bool:
        """True when both quote and trade contexts are alive."""
        return self.quote_ctx is not None and self.trade_ctx is not None and self.ft is not None

    def _resolved_trd_env(self):
        if self.ft is None:
            raise RuntimeError("Futu SDK not loaded. Call connect() first.")
        return getattr(self.ft.TrdEnv, self.config.trd_env, self.ft.TrdEnv.SIMULATE)

    def _safe_trade_call(self, method_name: str, **kwargs):
        if self.trade_ctx is None or self.ft is None:
            self._futu_fail_count += 1
            if self._futu_fail_count >= self._FUTU_FAIL_THRESHOLD and not self._futu_degraded:
                self._futu_degraded = True
                self.logger.warning(
                    "[FUTU_DEGRADED] %d consecutive trade failures — switching to yfinance/state.json fallback mode",
                    self._futu_fail_count,
                )
            raise RuntimeError("FutuConnector not connected")
        try:
            method = getattr(self.trade_ctx, method_name)
            ret, data = method(trd_env=self._resolved_trd_env(), **kwargs)
            if ret != self.ft.RET_OK:
                raise RuntimeError(self._build_trade_error(f"{method_name} failed", data))
            # Successful call — reset degraded state
            if self._futu_degraded:
                self.logger.info("[FUTU_RECOVERED] Trade call succeeded — exiting degraded mode")
                self._futu_degraded = False
            self._futu_fail_count = 0
            return data
        except Exception:
            self._futu_fail_count += 1
            if self._futu_fail_count >= self._FUTU_FAIL_THRESHOLD and not self._futu_degraded:
                self._futu_degraded = True
                self.logger.warning(
                    "[FUTU_DEGRADED] %d consecutive trade failures — switching to yfinance/state.json fallback mode",
                    self._futu_fail_count,
                )
            raise

    def _safe_trade_call_legacy(self, method_name: str, **kwargs):
        """Fallback for SDK methods that do not accept `trd_env`."""
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")
        method = getattr(self.trade_ctx, method_name)
        ret, data = method(**kwargs)
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

        if not self.discovered_accounts.empty and "acc_id" in self.discovered_accounts.columns:
            # Populate global target_acc_id for backward compat
            if self.config.target_acc_id is None:
                self.config.target_acc_id = int(self.discovered_accounts.iloc[0]["acc_id"])

            # Build per-market account ID map from trd_market_auth when available
            for _, row in self.discovered_accounts.iterrows():
                acc_id = int(row["acc_id"])
                # trd_market_auth is a list of authorized market codes; fall back to market_prefix
                auth_markets = row.get("trd_market_auth") if "trd_market_auth" in row.index else None
                if auth_markets and isinstance(auth_markets, (list, tuple, set)):
                    for mkt in auth_markets:
                        mkt_upper = str(mkt).upper()
                        if mkt_upper in ("HK", "US") and mkt_upper not in self.account_ids:
                            self.account_ids[mkt_upper] = acc_id
                else:
                    # No market auth info — use first account per market context
                    for mkt in self.trade_ctxs:
                        if mkt not in self.account_ids:
                            self.account_ids[mkt] = acc_id

            self.logger.info(
                "Account discovery: global_acc_id=%s per_market=%s",
                self.config.target_acc_id,
                self.account_ids,
            )

        # Try per-market account discovery from each trade context independently
        for market, ctx in self.trade_ctxs.items():
            if market in self.account_ids:
                continue
            try:
                ret, mkt_data = ctx.get_acc_list()
                if ret == (self.ft.RET_OK if self.ft else 0) and mkt_data is not None and not mkt_data.empty:
                    if "acc_id" in mkt_data.columns:
                        self.account_ids[market] = int(mkt_data.iloc[0]["acc_id"])
                        self.logger.info("Per-market account %s: acc_id=%s", market, self.account_ids[market])
            except Exception as exc:
                self.logger.debug("Per-market account discovery for %s failed: %s", market, exc)

        return self.discovered_accounts

    def _account_kwargs(self) -> dict[str, Any]:
        if self.config.target_acc_id is None:
            return {}
        return {"acc_id": int(self.config.target_acc_id)}

    def _account_kwargs_for_market(self, market: str) -> dict[str, Any]:
        """Return acc_id kwargs scoped to a specific market, falling back to global."""
        mkt = str(market).upper()
        acc_id = self.account_ids.get(mkt) or self.config.target_acc_id
        if acc_id is None:
            return {}
        return {"acc_id": int(acc_id)}

    def heartbeat(self) -> bool:
        try:
            _ = self._safe_trade_call("accinfo_query", **self._account_kwargs())
            return True
        except Exception as exc:
            self.logger.warning("Futu heartbeat failed: %s", exc)
            return False

    def resolve_symbol(self, symbol: str) -> tuple[str, str]:
        """Resolve a logical symbol to a Futu broker code and market prefix.

        HK symbols (ending in '.HK') always use the 'HK' prefix regardless of
        current market_prefix. All others use self.config.market_prefix.

        Returns:
            (broker_code, market_prefix), e.g.
            'AAPL'    -> ('US.AAPL',    'US')
            '0700.HK' -> ('HK.0700.HK', 'HK')
        """
        if str(symbol).upper().endswith(".HK"):
            prefix = "HK"
        else:
            prefix = self.config.market_prefix or "US"
        return f"{prefix}.{symbol}", prefix

    def set_market_prefix(self, prefix: str) -> None:
        """Update the active market prefix (called when switching HK ↔ US session)."""
        self.config.market_prefix = str(prefix).upper()
        self.logger.info("Market prefix set to %s", self.config.market_prefix)

    def subscribe_symbols(self, symbols: list[str]) -> None:
        """Subscribe to real-time quotes for the given symbol list.

        No-op if Futu quote context is not connected — the system falls back to
        yfinance for quote data, so this is non-fatal.
        """
        if self.quote_ctx is None or self.ft is None:
            self.logger.debug("subscribe_symbols: quote_ctx not connected, skipping")
            return
        codes = [self.resolve_symbol(s)[0] for s in symbols]
        try:
            ret, err = self.quote_ctx.subscribe(codes, [self.ft.SubType.QUOTE], subscribe_push=True)
            if ret == self.ft.RET_OK:
                self.logger.info("Subscribed to real-time quotes for %d symbols", len(codes))
            else:
                self.logger.warning("Quote subscription failed: %s", err)
        except Exception as exc:
            self.logger.warning("subscribe_symbols error (non-fatal): %s", exc)

    def reconnect(self, max_retries: int = 10, base_delay_seconds: int = 5) -> None:
        hard_attempt_cap = 3
        hard_elapsed_budget_seconds = 30.0
        bounded_delays_seconds = (2, 4, 8)

        max_retries = min(max(1, int(max_retries)), hard_attempt_cap)
        started = time.time()
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            elapsed = time.time() - started
            if elapsed >= hard_elapsed_budget_seconds:
                raise RuntimeError(
                    "RECONNECT_BUDGET_EXCEEDED: "
                    f"elapsed={elapsed:.2f}s attempts={attempt - 1} budget={hard_elapsed_budget_seconds:.2f}s"
                )
            try:
                self.logger.warning("Reconnect attempt %d/%d", attempt, max_retries)
                self._safe_close_contexts()
                self.connect()
                self.logger.info("Reconnect succeeded on attempt %d", attempt)
                return
            except Exception as exc:  # pragma: no cover - defensive backoff logging
                last_exc = exc
                if attempt >= max_retries:
                    break
                delay = bounded_delays_seconds[min(attempt - 1, len(bounded_delays_seconds) - 1)]
                remaining_budget = hard_elapsed_budget_seconds - (time.time() - started)
                if remaining_budget <= 0:
                    raise RuntimeError(
                        "RECONNECT_BUDGET_EXCEEDED: "
                        f"elapsed={time.time() - started:.2f}s attempts={attempt} budget={hard_elapsed_budget_seconds:.2f}s"
                    ) from exc
                delay = min(delay, remaining_budget)
                self.logger.warning("Reconnect attempt %d failed: %s; retrying in %ss", attempt, exc, delay)
                time.sleep(delay)
        elapsed = time.time() - started
        if elapsed >= hard_elapsed_budget_seconds:
            raise RuntimeError(
                "RECONNECT_BUDGET_EXCEEDED: "
                f"elapsed={elapsed:.2f}s attempts={max_retries} budget={hard_elapsed_budget_seconds:.2f}s"
            ) from last_exc
        raise RuntimeError(f"RECONNECT_BUDGET_EXCEEDED: attempts={max_retries}/{hard_attempt_cap}; last_error={last_exc}")

    def get_latest_quote(self, symbol: str, use_cache: bool = True) -> dict:
        # Use resolve_symbol so HK stocks get broker code "HK.0700.HK" not "US.0700.HK"
        broker_code, market = self.resolve_symbol(symbol)
        provider_errors: list[str] = []

        # Cache-first path when use_cache=True
        if use_cache:
            cached = self._get_cached_quote(symbol)
            if cached is not None:
                return cached

        # yfinance returns 404 for HK stocks ("possibly delisted"); skip it for HK market.
        # HK fallback order: ef → futu → yf (last resort only).
        is_hk = market.upper() == "HK" or symbol.upper().endswith(".HK")
        provider_order = ("ef", "futu", "yf") if is_hk else ("yf", "ef", "futu")
        for provider in provider_order:
            try:
                if provider == "yf":
                    quote = self._get_latest_quote_yfinance(symbol)
                elif provider == "ef":
                    quote = self._get_latest_quote_efinance(symbol)
                else:
                    if self.quote_ctx is None or self.ft is None:
                        raise RuntimeError("not_connected")
                    ret, df = self.quote_ctx.get_stock_quote([broker_code])
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

        raise RuntimeError(
            f"Quote query failed for {broker_code}. Providers exhausted: {'; '.join(provider_errors)}"
        )

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
        if self._futu_degraded:
            self.logger.debug("get_sync_assets: degraded mode — returning empty assets")
            return {"total_assets": 0.0, "cash": 0.0, "power": 0.0}
        data = self._safe_trade_call("accinfo_query", **self._account_kwargs())
        if data is None or data.empty:
            raise RuntimeError("accinfo_query returned empty dataset")
        row = data.iloc[0]
        return {"total_assets": float(row.get("total_assets", 0.0)), "cash": float(row.get("cash", 0.0)), "power": float(row.get("power", 0.0))}

    def get_sync_assets_for_market(self, market: str) -> dict:
        """Sync account assets for a specific market using the per-market trade context."""
        if self._futu_degraded:
            self.logger.debug("get_sync_assets_for_market(%r): degraded mode", market)
            return {"total_assets": 0.0, "cash": 0.0, "power": 0.0}
        mkt = str(market).upper()
        ctx = self.trade_ctxs.get(mkt)
        if ctx is None:
            return self.get_sync_assets()
        kwargs = self._account_kwargs_for_market(mkt)
        kwargs["trd_env"] = self._resolved_trd_env()
        try:
            ret, data = ctx.accinfo_query(**kwargs)
            if ret != (self.ft.RET_OK if self.ft else 0) or data is None or data.empty:
                raise RuntimeError(f"accinfo_query for {mkt} returned empty")
            row = data.iloc[0]
            return {
                "total_assets": float(row.get("total_assets", 0.0)),
                "cash": float(row.get("cash", 0.0)),
                "power": float(row.get("power", 0.0)),
            }
        except Exception as exc:
            self.logger.warning("Asset sync for market %s failed: %s", mkt, exc)
            return {"total_assets": 0.0, "cash": 0.0, "power": 0.0}

    def get_sync_positions(self) -> pd.DataFrame:
        if self._futu_degraded:
            self.logger.debug("get_sync_positions: degraded mode — returning empty positions")
            return pd.DataFrame()
        data = self._safe_trade_call("position_list_query", **self._account_kwargs())
        return pd.DataFrame() if data is None else data.copy()

    def get_sync_positions_for_market(self, market: str) -> pd.DataFrame:
        """Sync positions for a specific market using the per-market trade context."""
        if self._futu_degraded:
            self.logger.debug("get_sync_positions_for_market(%r): degraded mode", market)
            return pd.DataFrame()
        mkt = str(market).upper()
        ctx = self.trade_ctxs.get(mkt)
        if ctx is None:
            return self.get_sync_positions()
        kwargs = self._account_kwargs_for_market(mkt)
        kwargs["trd_env"] = self._resolved_trd_env()
        try:
            ret, data = ctx.position_list_query(**kwargs)
            if ret != (self.ft.RET_OK if self.ft else 0):
                raise RuntimeError(f"position_list_query for {mkt} returned error: {data}")
            return pd.DataFrame() if data is None else data.copy()
        except Exception as exc:
            self.logger.warning("Position sync for market %s failed: %s", mkt, exc)
            return pd.DataFrame()

    def get_sync_assets_all_markets(self) -> dict:
        """Sum account assets across all active market trade contexts.

        When HK and US trade contexts are both active, adds total_assets, cash,
        and power from each market's account so the caller sees the combined
        buying power of the full portfolio.

        Falls back to the legacy single-context ``get_sync_assets()`` when no
        per-market contexts are registered.
        """
        if not self.trade_ctxs:
            return self.get_sync_assets()

        combined = {"total_assets": 0.0, "cash": 0.0, "power": 0.0}
        any_ok = False
        for market in self.trade_ctxs:
            try:
                mkt_assets = self.get_sync_assets_for_market(market)
                combined["total_assets"] += mkt_assets.get("total_assets", 0.0)
                combined["cash"] += mkt_assets.get("cash", 0.0)
                combined["power"] += mkt_assets.get("power", 0.0)
                any_ok = True
            except Exception as exc:
                self.logger.warning("Asset sync for market %s failed in all_markets: %s", market, exc)

        if not any_ok:
            return self.get_sync_assets()
        return combined

    def get_sync_positions_all_markets(self) -> pd.DataFrame:
        """Fetch positions from all active market trade contexts and combine.

        Falls back to the legacy single-context ``get_sync_positions()`` when no
        per-market contexts are available.
        """
        if not self.trade_ctxs:
            return self.get_sync_positions()

        frames: list[pd.DataFrame] = []
        for market in self.trade_ctxs:
            df = self.get_sync_positions_for_market(market)
            if not df.empty:
                frames.append(df)

        if not frames:
            return pd.DataFrame()
        if len(frames) == 1:
            return frames[0]
        combined = pd.concat(frames, ignore_index=True)
        combined.drop_duplicates(subset=["code"] if "code" in combined.columns else None, keep="last", inplace=True)
        return combined

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        """Poll single-order status from broker and normalize key fields."""
        order_id_str = str(order_id).strip()
        if not order_id_str:
            raise ValueError("order_id is required")

        kwargs = {"order_id": order_id_str, **self._account_kwargs()}
        try:
            data = self._safe_trade_call("order_list_query", **kwargs)
        except TypeError as exc:
            if "trd_env" not in str(exc):
                raise
            data = self._safe_trade_call_legacy("order_list_query", **kwargs)

        if data is None or data.empty:
            raise RuntimeError(f"order_list_query returned empty dataset for order_id={order_id_str}")
        row = data.iloc[0].to_dict()
        normalized = {str(k).strip().lower(): v for k, v in row.items()}
        status_text = str(
            normalized.get("order_status")
            or normalized.get("status")
            or normalized.get("orderstate")
            or "UNKNOWN"
        )
        avg_price = float(
            normalized.get("dealt_avg_price")
            or normalized.get("avg_price")
            or normalized.get("price")
            or 0.0
        )
        dealt_qty = int(float(normalized.get("dealt_qty") or normalized.get("qty") or 0))
        return {
            "order_id": order_id_str,
            "status": status_text,
            "filled_avg_price": avg_price,
            "dealt_qty": dealt_qty,
            "raw": row,
        }

    def wait_for_fill(self, order_id: str, timeout_seconds: float = 60.0, poll_interval_seconds: float = 2.0) -> dict[str, Any]:
        """Wait until an order is filled/cancelled and return the final fill summary."""
        terminal_filled = ("filled", "all_filled", "已成交", "全部成交")
        terminal_failed = ("cancel", "canceled", "cancelled", "failed", "rejected", "部分撤单", "拒绝")
        started = time.time()
        while True:
            status = self.get_order_status(order_id)
            lowered = str(status.get("status", "")).strip().lower()
            if any(token in lowered for token in terminal_filled):
                return status
            if any(token in lowered for token in terminal_failed):
                raise RuntimeError(f"order {order_id} ended without fill: {status}")
            if (time.time() - started) >= timeout_seconds:
                raise TimeoutError(f"wait_for_fill timeout for order {order_id}: last_status={status}")
            time.sleep(max(0.1, float(poll_interval_seconds)))

    def get_open_orders(self) -> pd.DataFrame:
        """Retrieve current order list for reconnect-time reconciliation."""
        kwargs = self._account_kwargs()
        try:
            data = self._safe_trade_call("order_list_query", **kwargs)
        except TypeError as exc:
            if "trd_env" not in str(exc):
                raise
            data = self._safe_trade_call_legacy("order_list_query", **kwargs)
        return pd.DataFrame() if data is None else data.copy()

    def extract_order_id(self, order_result: object) -> str:
        if order_result is None:
            raise RuntimeError("order_result is empty")
        if isinstance(order_result, pd.DataFrame):
            if order_result.empty:
                raise RuntimeError("order_result dataframe is empty")
            row = order_result.iloc[0].to_dict()
            for key in ("order_id", "orderid"):
                if key in row and str(row[key]).strip():
                    return str(row[key]).strip()
        if isinstance(order_result, dict):
            for key in ("order_id", "orderid"):
                if key in order_result and str(order_result[key]).strip():
                    return str(order_result[key]).strip()
        raise RuntimeError(f"unable to extract order_id from order result: {order_result}")

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

    def _round_price(self, price: float, symbol: str) -> float:
        """Round price to the precision Futu API requires.

        US stocks: 2 decimal places (cent precision).
        HK stocks: 3 decimal places.
        """
        decimals = 3 if symbol.endswith(".HK") or self.config.market_prefix == "HK" else 2
        return round(price, decimals)

    def place_order(self, symbol: str, qty: int, side: str, price: Optional[float] = None) -> object:
        if self.ft is None:
            raise RuntimeError("FutuConnector not connected")
        if qty <= 0:
            raise ValueError("qty must be > 0")

        # Use resolve_symbol so HK stocks never get "US.0700.HK" as the broker code
        broker_code_str, market = self.resolve_symbol(symbol)
        trade_ctx = self.get_trade_ctx(market)

        raw_price = float(price) if price is not None else self.get_order_reference_price(symbol, side, fallback_price=0.0)
        limit_price = self._round_price(raw_price, symbol)
        if limit_price != raw_price:
            self.logger.debug("Price rounded for %s: %.6f -> %.4f", symbol, raw_price, limit_price)
        self.validate_trading_ready(symbol=symbol, qty=qty, side=side, est_price=limit_price)
        ret, data = trade_ctx.place_order(
            price=limit_price,
            qty=qty,
            code=broker_code_str,
            trd_side=self.ft.TrdSide.BUY if side.upper() == "BUY" else self.ft.TrdSide.SELL,
            order_type=self.ft.OrderType.NORMAL,
            trd_env=self._resolved_trd_env(),
            **self._account_kwargs_for_market(market),
        )
        if ret != self.ft.RET_OK:
            raise RuntimeError(self._build_trade_error("Order placement failed", data))
        return data
