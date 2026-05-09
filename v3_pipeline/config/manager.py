"""Single typed config loader for the Kiro Quant V3 trading system.

Precedence (highest to lowest):
  1. Environment variables
  2. config.json values
  3. Dataclass field defaults

Each env override that fires is logged at INFO level so runtime behavior is
always visible in the log stream.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Field-level env override registry ────────────────────────────────────────
# Maps (section, field_name) -> env var name.  When the env var is set, it
# overrides whatever is in config.json.

_ENV_OVERRIDES: dict[tuple[str, str], str] = {
    ("futu", "host"):            "FUTU_OPEND_HOST",
    ("futu", "port"):            "FUTU_OPEND_PORT",
    ("futu", "trd_env"):         "FUTU_TRD_ENV",
    ("futu", "target_acc_id"):   "FUTU_TARGET_ACC_ID",
    ("futu", "trade_password"):  "FUTU_TRADE_PWD",
    ("futu", "opend_web_port"):  "FUTU_OPEND_WEB_PORT",
    ("v3_live", "polling_seconds"): "V3_POLLING_SECONDS",
    ("v3_live", "auto_trade"):      "V3_AUTO_TRADE",
    ("v3_live", "paper_trading"):   "V3_PAPER_TRADING",
    ("v3_live", "max_positions"):   "V3_MAX_POSITIONS",
    ("app", "auto_trade"):          "V3_AUTO_TRADE",
    ("app", "paper_trading"):       "V3_PAPER_TRADING",
}


def _apply_env(section: str, name: str, raw: Any, cast) -> Any:
    """Return env-overridden value if the mapped env var is set, else raw."""
    env_var = _ENV_OVERRIDES.get((section, name))
    if not env_var:
        return raw
    val = os.environ.get(env_var)
    if val is None:
        return raw
    try:
        result = cast(val)
    except (ValueError, TypeError) as exc:
        logger.warning(
            "ConfigManager: env var %s=%r cannot be cast to %s; using config.json value. Error: %s",
            env_var, val, cast.__name__, exc,
        )
        return raw
    logger.info("ConfigManager: env override %s=%r -> %s.%s", env_var, val, section, name)
    return result


def _bool_from_str(v: str) -> bool:
    return v.lower() not in {"0", "false", "no", "off", ""}


# ── Typed config dataclasses ──────────────────────────────────────────────────

@dataclass
class FutuCfg:
    host: str = "127.0.0.1"
    port: int = 11111
    trd_env: str = "SIMULATE"
    target_acc_id: Optional[int] = None
    opend_web_port: int = 18889
    trade_password: str = ""
    market_prefix: str = "US"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.host:
            errors.append("futu.host must not be empty")
        if not (1 <= self.port <= 65535):
            errors.append(f"futu.port must be 1-65535, got {self.port}")
        if self.trd_env not in {"SIMULATE", "REAL"}:
            errors.append(f"futu.trd_env must be SIMULATE or REAL, got {self.trd_env!r}")
        return errors


@dataclass
class CapitalBucketsCfg:
    fractions: dict[str, float] = field(
        default_factory=lambda: {"long": 0.5, "mid": 0.3, "short": 0.1, "reserve": 0.1, "avoid": 0.0}
    )
    thresholds: dict[str, float] = field(
        default_factory=lambda: {"long": 0.002, "mid": 0.0015, "short": 0.0012, "avoid": 0.01}
    )
    by_symbol: dict[str, str] = field(default_factory=dict)


@dataclass
class V3LiveCfg:
    symbols_list: list[str] = field(default_factory=list)
    polling_seconds: int = 60
    auto_trade: bool = True
    paper_trading: bool = False
    max_positions: int = 10
    buy_cooldown_cycles: int = 3
    swing_buy_confirmation_count: int = 2
    swing_block_on_model_sell: bool = False
    stop_loss_pct: float = 0.02
    quick_take_profit_pct: float = 0.02
    max_hold_bars: int = 30
    market_mode: str = "AUTO"
    rsi_oversold: float = 40.0
    rsi_overbought: float = 75.75
    macd_signal: float = 0.005
    xgb_confidence: float = 0.1
    min_hold_minutes: int = 45
    time_exit_near_entry_pct: float = 0.005
    rsi_extreme_oversold: int = 35
    bb_position_threshold: float = 0.2
    vix_dynamic_confidence_enabled: bool = True
    short_enabled: bool = True
    rsi_deep_overbought: int = 80
    macd_negative_required: bool = True
    sma_filter_short: bool = True
    short_min_confidence_threshold: float = 0.1
    short_sentiment_max: float = 0.1
    short_stop_loss: float = 0.015
    short_take_profit: float = 0.02
    short_trailing_stop_trigger: float = 0.015
    short_trailing_stop_lock: float = 0.0
    prediction_thresholds: dict[str, float] = field(default_factory=dict)
    capital_buckets: CapitalBucketsCfg = field(default_factory=CapitalBucketsCfg)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.max_positions < 1:
            errors.append(f"v3_live.max_positions must be >= 1, got {self.max_positions}")
        if self.polling_seconds < 1:
            errors.append(f"v3_live.polling_seconds must be >= 1, got {self.polling_seconds}")
        if not (0.0 <= self.stop_loss_pct <= 1.0):
            errors.append(f"v3_live.stop_loss_pct out of range: {self.stop_loss_pct}")
        if not (0.0 <= self.quick_take_profit_pct <= 1.0):
            errors.append(f"v3_live.quick_take_profit_pct out of range: {self.quick_take_profit_pct}")
        return errors


@dataclass
class ModelCfg:
    input_dim: int = 26
    markets: dict[str, str] = field(
        default_factory=lambda: {"HK": "v3_hk_stocks", "US": "v3_us_stocks", "IDLE": "v3_us_stocks"}
    )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.input_dim < 1:
            errors.append(f"model.input_dim must be >= 1, got {self.input_dim}")
        return errors


@dataclass
class AppConfig:
    """Top-level application config, assembled from config.json + env overrides."""
    auto_trade: bool = True
    paper_trading: bool = False
    posture: str = "risk_on"
    futu: FutuCfg = field(default_factory=FutuCfg)
    v3_live: V3LiveCfg = field(default_factory=V3LiveCfg)
    model: ModelCfg = field(default_factory=ModelCfg)

    def validate(self) -> list[str]:
        errors: list[str] = []
        errors.extend(self.futu.validate())
        errors.extend(self.v3_live.validate())
        errors.extend(self.model.validate())
        return errors


# ── ConfigManager ─────────────────────────────────────────────────────────────

class ConfigManager:
    """Single typed config loader.

    Usage:
        mgr = ConfigManager("config.json")
        cfg = mgr.app      # AppConfig (validated)
        futu = mgr.futu    # FutuCfg shortcut
        live = mgr.live    # V3LiveCfg shortcut
    """

    def __init__(self, config_path: str = "config.json", *, strict: bool = False) -> None:
        self._path = Path(config_path)
        self._strict = strict
        self._raw: dict[str, Any] = {}
        self._app: Optional[AppConfig] = None
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._raw = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(self._raw, dict):
                    logger.warning("ConfigManager: %s is not a JSON object; using defaults", self._path)
                    self._raw = {}
                else:
                    logger.info("ConfigManager: loaded %s (%d top-level keys)", self._path, len(self._raw))
            except json.JSONDecodeError as exc:
                logger.error("ConfigManager: failed to parse %s: %s", self._path, exc)
                self._raw = {}
        else:
            logger.warning("ConfigManager: config file not found at %s; using defaults", self._path)
            self._raw = {}

        self._app = self._build_app()
        errors = self._app.validate()
        if errors:
            msg = "ConfigManager validation errors:\n" + "\n".join(f"  - {e}" for e in errors)
            if self._strict:
                raise ValueError(msg)
            logger.warning(msg)

    def _build_futu(self) -> FutuCfg:
        raw = self._raw.get("futu") or self._raw.get("futu_api_config") or {}
        if not isinstance(raw, dict):
            raw = {}

        host = _apply_env("futu", "host", raw.get("host", "127.0.0.1"), str)
        port = _apply_env("futu", "port", int(raw.get("port", 11111)), int)
        trd_env = _apply_env("futu", "trd_env", str(raw.get("trd_env", "SIMULATE")).upper(), str)
        raw_acc = raw.get("target_acc_id")
        target_acc_id = _apply_env(
            "futu", "target_acc_id",
            int(raw_acc) if raw_acc is not None else None,
            lambda v: int(v) if v else None,
        )
        trade_password = _apply_env(
            "futu", "trade_password",
            str(raw.get("trade_password", raw.get("trade_pwd", ""))),
            str,
        )
        opend_web_port = _apply_env("futu", "opend_web_port", int(raw.get("opend_web_port", 18889)), int)
        market_prefix = str(raw.get("market_prefix", raw.get("market", "US")))

        return FutuCfg(
            host=host,
            port=port,
            trd_env=trd_env,
            target_acc_id=target_acc_id,
            trade_password=trade_password,
            opend_web_port=opend_web_port,
            market_prefix=market_prefix,
        )

    def _build_v3_live(self) -> V3LiveCfg:
        raw = self._raw.get("v3_live") or {}
        if not isinstance(raw, dict):
            raw = {}

        cb_raw = raw.get("capital_buckets") or {}
        buckets = CapitalBucketsCfg(
            fractions=cb_raw.get("fractions", {"long": 0.5, "mid": 0.3, "short": 0.1, "reserve": 0.1, "avoid": 0.0}),
            thresholds=cb_raw.get("thresholds", {"long": 0.002, "mid": 0.0015, "short": 0.0012, "avoid": 0.01}),
            by_symbol=cb_raw.get("by_symbol", {}),
        )

        top_auto_trade = self._raw.get("auto_trade", True)
        top_paper_trading = self._raw.get("paper_trading", False)
        auto_trade = _apply_env("v3_live", "auto_trade", bool(raw.get("auto_trade", top_auto_trade)), _bool_from_str)
        paper_trading = _apply_env("v3_live", "paper_trading", bool(raw.get("paper_trading", top_paper_trading)), _bool_from_str)
        polling_seconds = _apply_env("v3_live", "polling_seconds", int(raw.get("polling_seconds", 60)), int)
        max_positions = _apply_env("v3_live", "max_positions", int(raw.get("max_positions", 10)), int)

        return V3LiveCfg(
            symbols_list=list(raw.get("symbols_list", [])),
            polling_seconds=polling_seconds,
            auto_trade=auto_trade,
            paper_trading=paper_trading,
            max_positions=max_positions,
            buy_cooldown_cycles=int(raw.get("buy_cooldown_cycles", 3)),
            swing_buy_confirmation_count=int(raw.get("swing_buy_confirmation_count", 2)),
            swing_block_on_model_sell=bool(raw.get("swing_block_on_model_sell", False)),
            stop_loss_pct=float(raw.get("stop_loss_pct", 0.02)),
            quick_take_profit_pct=float(raw.get("quick_take_profit_pct", 0.02)),
            max_hold_bars=int(raw.get("max_hold_bars", 30)),
            market_mode=str(raw.get("market_mode", "AUTO")),
            rsi_oversold=float(raw.get("rsi_oversold", 40.0)),
            rsi_overbought=float(raw.get("rsi_overbought", 75.75)),
            macd_signal=float(raw.get("macd_signal", 0.005)),
            xgb_confidence=float(raw.get("xgb_confidence", 0.1)),
            min_hold_minutes=int(raw.get("min_hold_minutes", 45)),
            time_exit_near_entry_pct=float(raw.get("time_exit_near_entry_pct", 0.005)),
            rsi_extreme_oversold=int(raw.get("rsi_extreme_oversold", 35)),
            bb_position_threshold=float(raw.get("bb_position_threshold", 0.2)),
            vix_dynamic_confidence_enabled=bool(raw.get("vix_dynamic_confidence_enabled", True)),
            short_enabled=bool(raw.get("short_enabled", True)),
            rsi_deep_overbought=int(raw.get("rsi_deep_overbought", 80)),
            macd_negative_required=bool(raw.get("macd_negative_required", True)),
            sma_filter_short=bool(raw.get("sma_filter_short", True)),
            short_min_confidence_threshold=float(raw.get("short_min_confidence_threshold", 0.1)),
            short_sentiment_max=float(raw.get("short_sentiment_max", 0.1)),
            short_stop_loss=float(raw.get("short_stop_loss", 0.015)),
            short_take_profit=float(raw.get("short_take_profit", 0.02)),
            short_trailing_stop_trigger=float(raw.get("short_trailing_stop_trigger", 0.015)),
            short_trailing_stop_lock=float(raw.get("short_trailing_stop_lock", 0.0)),
            prediction_thresholds=dict(raw.get("prediction_thresholds", {})),
            capital_buckets=buckets,
        )

    def _build_model(self) -> ModelCfg:
        raw = self._raw.get("model") or {}
        if not isinstance(raw, dict):
            raw = {}
        return ModelCfg(
            input_dim=int(raw.get("input_dim", 26)),
            markets=dict(raw.get("markets", {"HK": "v3_hk_stocks", "US": "v3_us_stocks", "IDLE": "v3_us_stocks"})),
        )

    def _build_app(self) -> AppConfig:
        top_auto_trade = _apply_env(
            "app", "auto_trade",
            bool(self._raw.get("auto_trade", True)),
            _bool_from_str,
        )
        top_paper_trading = _apply_env(
            "app", "paper_trading",
            bool(self._raw.get("paper_trading", False)),
            _bool_from_str,
        )
        return AppConfig(
            auto_trade=top_auto_trade,
            paper_trading=top_paper_trading,
            posture=str(self._raw.get("posture", "risk_on")),
            futu=self._build_futu(),
            v3_live=self._build_v3_live(),
            model=self._build_model(),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def app(self) -> AppConfig:
        return self._app  # type: ignore[return-value]

    @property
    def futu(self) -> FutuCfg:
        return self._app.futu  # type: ignore[union-attr]

    @property
    def live(self) -> V3LiveCfg:
        return self._app.v3_live  # type: ignore[union-attr]

    @property
    def model(self) -> ModelCfg:
        return self._app.model  # type: ignore[union-attr]

    def reload(self) -> None:
        """Re-read config.json from disk and rebuild all typed objects."""
        self._load()
        logger.info("ConfigManager: reloaded from %s", self._path)

    def raw(self) -> dict[str, Any]:
        """Return the raw parsed JSON dict (read-only copy)."""
        return dict(self._raw)


# ── Module-level convenience ──────────────────────────────────────────────────

_default_manager: Optional[ConfigManager] = None


def load_config(config_path: str = "config.json", *, strict: bool = False) -> ConfigManager:
    """Return the module-level ConfigManager, creating it on first call.

    Subsequent calls with the same path return the cached instance.
    Call ``mgr.reload()`` to pick up on-disk changes without creating a new one.
    """
    global _default_manager
    if _default_manager is None or str(_default_manager._path) != str(Path(config_path)):
        _default_manager = ConfigManager(config_path, strict=strict)
    return _default_manager
