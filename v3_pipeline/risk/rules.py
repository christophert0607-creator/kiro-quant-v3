from dataclasses import dataclass


@dataclass
class RiskRulesConfig:
    max_risk_per_trade: float = 0.02
    max_daily_risk: float = 0.05
    max_positions: int = 10
    max_total_exposure: float = 0.80


class RiskRulesEngine:
    """Hard risk-rule checks for position and portfolio level constraints."""

    def __init__(self, config: RiskRulesConfig | None = None) -> None:
        self.config = config or RiskRulesConfig()

    def check_trade_risk(self, proposed_trade_risk_fraction: float) -> bool:
        return proposed_trade_risk_fraction <= self.config.max_risk_per_trade

    def check_daily_risk(self, current_daily_risk_fraction: float, proposed_trade_risk_fraction: float) -> bool:
        return (current_daily_risk_fraction + proposed_trade_risk_fraction) <= self.config.max_daily_risk

    def check_position_limit(self, current_positions: int) -> bool:
        return current_positions < self.config.max_positions

    def check_total_exposure(self, current_exposure_fraction: float, proposed_exposure_fraction: float) -> bool:
        return (current_exposure_fraction + proposed_exposure_fraction) <= self.config.max_total_exposure

    def can_open_trade(
        self,
        *,
        proposed_trade_risk_fraction: float,
        current_daily_risk_fraction: float,
        current_positions: int,
        current_exposure_fraction: float,
        proposed_exposure_fraction: float,
    ) -> bool:
        return all(
            (
                self.check_trade_risk(proposed_trade_risk_fraction),
                self.check_daily_risk(current_daily_risk_fraction, proposed_trade_risk_fraction),
                self.check_position_limit(current_positions),
                self.check_total_exposure(current_exposure_fraction, proposed_exposure_fraction),
            )
        )
