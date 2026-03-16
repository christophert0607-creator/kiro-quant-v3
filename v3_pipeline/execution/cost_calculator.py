from dataclasses import dataclass


@dataclass
class TransactionCostConfig:
    commission_rate_us: float = 0.001
    commission_rate_hk: float = 0.001
    stamp_duty_rate_hk: float = 0.001
    slippage_large_cap: float = 0.0005
    slippage_mid_cap: float = 0.001
    slippage_small_cap: float = 0.002


class TransactionCostCalculator:
    def __init__(self, config: TransactionCostConfig | None = None) -> None:
        self.config = config or TransactionCostConfig()

    def _is_hk_symbol(self, symbol: str) -> bool:
        return symbol.endswith(".HK") or symbol.startswith("HK.") or symbol.startswith("HKG.")

    def _slippage_rate(self, market_cap: str) -> float:
        normalized = (market_cap or "").lower()
        if normalized == "large_cap":
            return self.config.slippage_large_cap
        if normalized == "mid_cap":
            return self.config.slippage_mid_cap
        if normalized == "small_cap":
            return self.config.slippage_small_cap
        return self.config.slippage_mid_cap

    def calculate_total_cost(self, symbol: str, quantity: float, price: float, market_cap: str = "large_cap") -> dict:
        trade_value = max(0.0, float(quantity) * float(price))
        if trade_value <= 0:
            return {
                "commission": 0.0,
                "slippage": 0.0,
                "stamp_duty": 0.0,
                "total_cost": 0.0,
                "cost_rate": 0.0,
            }

        is_hk = self._is_hk_symbol(symbol)
        commission_rate = self.config.commission_rate_hk if is_hk else self.config.commission_rate_us
        commission = trade_value * commission_rate
        stamp_duty = trade_value * self.config.stamp_duty_rate_hk if is_hk else 0.0
        slippage = trade_value * self._slippage_rate(market_cap)

        total_cost = commission + stamp_duty + slippage
        cost_rate = total_cost / trade_value

        return {
            "commission": commission,
            "slippage": slippage,
            "stamp_duty": stamp_duty,
            "total_cost": total_cost,
            "cost_rate": cost_rate,
        }

    def should_execute_trade(self, expected_return_rate: float, estimated_cost_rate: float, cost_buffer: float = 0.20) -> bool:
        required_return = estimated_cost_rate * (1.0 + max(0.0, cost_buffer))
        return expected_return_rate > required_return
