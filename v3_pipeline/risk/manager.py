import logging
import sys
from dataclasses import dataclass


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
class RiskConfig:
    max_drawdown: float = 0.20
    max_position_fraction: float = 0.05
    trailing_stop_pct: float = 0.05


class RiskController:
    """Risk guardrails for live/backtest trading decisions."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self.logger = _build_stderr_logger(self.__class__.__name__)

    def circuit_breaker_triggered(self, equity_peak: float, current_equity: float) -> bool:
        if equity_peak <= 0:
            return False
        drawdown = max(0.0, (equity_peak - current_equity) / equity_peak)
        triggered = drawdown >= self.config.max_drawdown
        if triggered:
            self.logger.warning(
                "Circuit breaker triggered. drawdown=%.2f%% (limit=%.2f%%)",
                drawdown * 100,
                self.config.max_drawdown * 100,
            )
        return triggered

    def calculate_position_size(self, account_value: float, price: float) -> int:
        if account_value <= 0 or price <= 0:
            return 0
        budget = account_value * self.config.max_position_fraction
        qty = int(budget // price)
        self.logger.info(
            "Calculated position size qty=%d (account_value=%.2f, price=%.2f)",
            qty,
            account_value,
            price,
        )
        return max(qty, 0)

    def trailing_stop_price(self, highest_price_since_entry: float) -> float:
        if highest_price_since_entry <= 0:
            return 0.0
        stop = highest_price_since_entry * (1 - self.config.trailing_stop_pct)
        return float(stop)

    def should_stop_out(self, current_price: float, highest_price_since_entry: float) -> bool:
        stop = self.trailing_stop_price(highest_price_since_entry)
        hit = current_price <= stop if stop > 0 else False
        if hit:
            self.logger.warning(
                "Trailing stop hit. current=%.4f <= stop=%.4f",
                current_price,
                stop,
            )
        return hit
