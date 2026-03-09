import json
import logging
import os
import sys
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
    """Futu OpenAPI adapter for quotes, account sync, account discovery and order placement."""

    def __init__(self, config: Optional[FutuConfig] = None, config_json_path: str = "config.json") -> None:
        self.config = config or FutuConfig()
        self.logger = _build_stderr_logger(self.__class__.__name__)
        self.quote_ctx = None
        self.trade_ctx = None
        self.ft = None

        self.login_account: Optional[int] = None
        self.discovered_accounts: pd.DataFrame = pd.DataFrame()

        self._load_runtime_config(config_json_path)

    def _load_runtime_config(self, config_json_path: str) -> None:
        """Optional config.json override for target account selection."""
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
            self.logger.info(
                "Connected to Futu OpenD at %s:%s (trd_env=%s, web_port_expected=%s)",
                self.config.host,
                self.config.port,
                self.config.trd_env,
                self.config.opend_web_port,
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
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")
        method = getattr(self.trade_ctx, method_name)
        trd_env = self._resolved_trd_env()

        try:
            ret, data = method(trd_env=trd_env, **kwargs)
            if ret != self.ft.RET_OK:
                raise RuntimeError(f"{method_name} failed: {data}")
            return data
        except Exception as exc:
            raise RuntimeError(f"Trade API call {method_name} failed: {exc}") from exc

    def discover_accounts(self) -> pd.DataFrame:
        """Discover all trade accounts and log identity mapping details.

        Returns DataFrame with account metadata, including acc_id, account type and status.
        """
        try:
            data = self._safe_trade_call("get_acc_list")
            if data is None:
                self.discovered_accounts = pd.DataFrame()
                return self.discovered_accounts

            discovered = data.copy()
            for col in ["acc_id", "sim_acc_type", "trd_env", "acc_type", "card_num", "is_trd_enabled"]:
                if col not in discovered.columns:
                    discovered[col] = None

            self.discovered_accounts = discovered

            if "login_id" in discovered.columns and not discovered.empty:
                self.login_account = int(discovered.iloc[0].get("login_id")) if pd.notna(discovered.iloc[0].get("login_id")) else None

            self.logger.info("Account discovery (login_account/Bull-ID=%s):", self.login_account)
            for _, row in discovered.iterrows():
                status = "ACTIVE" if bool(row.get("is_trd_enabled", True)) else "DISABLED"
                self.logger.info(
                    " - acc_id=%s, acc_type=%s, sim_acc_type=%s, trd_env=%s, status=%s",
                    row.get("acc_id"),
                    row.get("acc_type"),
                    row.get("sim_acc_type"),
                    row.get("trd_env"),
                    status,
                )

            if self.config.target_acc_id is None and "acc_id" in discovered.columns and not discovered.empty:
                self.config.target_acc_id = int(discovered.iloc[0]["acc_id"])
                self.logger.info("Auto-selected target_acc_id=%s", self.config.target_acc_id)
            elif self.config.target_acc_id is not None:
                self.logger.info("Configured target_acc_id=%s", self.config.target_acc_id)

            return self.discovered_accounts
        except Exception as exc:
            self.logger.warning("Account discovery failed: %s", exc)
            self.discovered_accounts = pd.DataFrame()
            return self.discovered_accounts

    def _account_kwargs(self) -> dict[str, Any]:
        if self.config.target_acc_id is None:
            return {}
        return {"acc_id": int(self.config.target_acc_id)}

    def heartbeat(self) -> bool:
        """Connection heartbeat: validates trade context authorization/activity."""
        try:
            _ = self._safe_trade_call("accinfo_query", **self._account_kwargs())
            self.logger.info("Futu heartbeat OK (acc_id=%s)", self.config.target_acc_id)
            return True
        except Exception as exc:
            self.logger.warning("Futu heartbeat failed: %s", exc)
            return False

    def get_latest_quote(self, symbol: str) -> dict:
        if self.quote_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")

        code = f"{self.config.market_prefix}.{symbol}"
        try:
            ret, df = self.quote_ctx.get_stock_quote([code])
            if ret != self.ft.RET_OK or df.empty:
                raise RuntimeError(f"Failed to fetch quote for {code}: {df}")

            row = df.iloc[0]
            return {
                "Date": pd.Timestamp.now(),
                "Open": float(row.get("open_price", row.get("last_price", 0.0))),
                "High": float(row.get("high_price", row.get("last_price", 0.0))),
                "Low": float(row.get("low_price", row.get("last_price", 0.0))),
                "Close": float(row.get("last_price", 0.0)),
                "Volume": float(row.get("volume", 0.0)),
            }
        except Exception as exc:
            raise RuntimeError(f"Quote query failed for {code}: {exc}") from exc

    def get_sync_assets(self) -> dict:
        data = self._safe_trade_call("accinfo_query", **self._account_kwargs())
        if data is None or data.empty:
            raise RuntimeError("accinfo_query returned empty dataset")

        row = data.iloc[0]
        return {
            "total_assets": float(row.get("total_assets", 0.0)),
            "cash": float(row.get("cash", 0.0)),
            "power": float(row.get("power", 0.0)),
        }

    def get_sync_positions(self) -> pd.DataFrame:
        data = self._safe_trade_call("position_list_query", **self._account_kwargs())
        if data is None:
            return pd.DataFrame()
        return data.copy()

    def place_order(self, symbol: str, qty: int, side: str, price: Optional[float] = None) -> object:
        if self.trade_ctx is None or self.ft is None:
            raise RuntimeError("FutuConnector not connected")
        if qty <= 0:
            raise ValueError("qty must be > 0")

        code = f"{self.config.market_prefix}.{symbol}"
        trd_side = self.ft.TrdSide.BUY if side.upper() == "BUY" else self.ft.TrdSide.SELL
        order_type = self.ft.OrderType.NORMAL
        order_price = 0 if price is None else float(price)

        try:
            ret, data = self.trade_ctx.place_order(
                price=order_price,
                qty=qty,
                code=code,
                trd_side=trd_side,
                order_type=order_type,
                trd_env=self._resolved_trd_env(),
                **self._account_kwargs(),
            )
            if ret != self.ft.RET_OK:
                raise RuntimeError(f"Order placement failed: {data}")
            self.logger.info("Order placed: %s %d %s (acc_id=%s)", side.upper(), qty, symbol, self.config.target_acc_id)
            return data
        except Exception as exc:
            raise RuntimeError(f"Order placement exception for {code}: {exc}") from exc
