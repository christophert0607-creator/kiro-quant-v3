import logging
import math
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
    ruin_threshold: float = 0.15
    max_trade_var_95: float = 0.03
    max_daily_loss_fraction: float = 1.0
    max_trade_cvar_95: float | None = None


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

    def estimate_risk_of_ruin(
        self,
        win_rate: float,
        reward_risk_ratio: float,
        risk_fraction: float,
    ) -> float:
        """Deeptest-style approximation for ruin risk.

        A conservative proxy: ruin decreases when edge and RR improve, increases with
        risk_fraction. Values are clipped to [0,1].
        """
        p = min(max(win_rate, 1e-6), 1 - 1e-6)
        q = 1 - p
        b = max(reward_risk_ratio, 1e-6)
        edge = p - q / b
        if edge <= 0:
            return 1.0

        # approximate number of consecutive losses affordable
        loss_budget_steps = max(1.0, 1.0 / max(risk_fraction, 1e-6))
        ruin = (q / (p * b)) ** loss_budget_steps
        ruin = float(min(max(ruin, 0.0), 1.0))
        if math.isnan(ruin):
            return 1.0
        return ruin

    def allow_trade_with_ror(
        self,
        win_rate: float,
        reward_risk_ratio: float,
        risk_fraction: float,
        mc_var_95: float,
    ) -> bool:
        ror = self.estimate_risk_of_ruin(
            win_rate=win_rate,
            reward_risk_ratio=reward_risk_ratio,
            risk_fraction=risk_fraction,
        )
        # Treat ruin_threshold as minimum survival score to keep tuning intuitive at 0.1~0.2.
        # Example: threshold=0.15 means we allow trades with ror <= 0.85.
        survival_score = 1.0 - ror
        if survival_score < self.config.ruin_threshold:
            self.logger.warning(
                "ROR gate blocked trade. ror=%.3f survival=%.3f threshold=%.3f",
                ror,
                survival_score,
                self.config.ruin_threshold,
            )
            return False

        if not self.allow_trade_with_var_cvar(mc_var_95=mc_var_95):
            return False

        return True

    def allow_daily_loss(
        self,
        day_start_equity: float,
        current_equity: float,
    ) -> bool:
        """Evaluate whether intraday drawdown is within configured limits."""
        if day_start_equity <= 0:
            return True
        loss_fraction = max(0.0, (day_start_equity - current_equity) / day_start_equity)
        allowed = loss_fraction <= max(0.0, float(self.config.max_daily_loss_fraction))
        if not allowed:
            self.logger.warning(
                "Daily loss gate blocked trade. loss=%.2f%% limit=%.2f%%",
                loss_fraction * 100,
                self.config.max_daily_loss_fraction * 100,
            )
        return allowed

    def estimate_cvar_95(
        self,
        return_samples: list[float] | tuple[float, ...] | None = None,
        tail_losses_95: list[float] | tuple[float, ...] | None = None,
    ) -> float:
        """Estimate 95% CVaR from return samples or precomputed tail losses."""
        tail = list(tail_losses_95 or [])
        if not tail and return_samples:
            samples = sorted(float(x) for x in return_samples)
            if samples:
                cutoff_idx = max(0, int(math.ceil(len(samples) * 0.05)) - 1)
                var_cutoff = samples[cutoff_idx]
                tail = [value for value in samples if value <= var_cutoff]

        if not tail:
            return 0.0

        return float(sum(tail) / len(tail))

    def allow_trade_with_var_cvar(
        self,
        mc_var_95: float,
        return_samples: list[float] | tuple[float, ...] | None = None,
        tail_losses_95: list[float] | tuple[float, ...] | None = None,
    ) -> bool:
        # RISK BOUNDARY: VaR protects percentile loss, CVaR caps average tail severity.
        if mc_var_95 < -abs(self.config.max_trade_var_95):
            self.logger.warning(
                "VaR/CVaR gate blocked trade on VaR. var95=%.4f limit=-%.4f",
                mc_var_95,
                abs(self.config.max_trade_var_95),
            )
            return False

        cvar_95 = self.estimate_cvar_95(return_samples=return_samples, tail_losses_95=tail_losses_95)
        max_cvar = self.config.max_trade_cvar_95
        if max_cvar is not None and cvar_95 < -abs(max_cvar):
            self.logger.warning(
                "VaR/CVaR gate blocked trade on CVaR. cvar95=%.4f limit=-%.4f",
                cvar_95,
                abs(max_cvar),
            )
            return False

        return True
