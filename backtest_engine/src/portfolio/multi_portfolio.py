from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .portfolio import Portfolio, Trade
from ..risk.controls import check_daily_loss, check_max_drawdown_current


@dataclass
class PortfolioResult:
    market_code: str
    equity: float
    trades: List[Trade]
    halted: bool
    halt_reason: Optional[str]


class MultiPortfolioManager:
    def __init__(
        self,
        market_codes: List[str],
        initial_capital: float,
        max_positions: int,
        max_single_position: float,
        daily_loss_limit: float,
        max_drawdown_limit: float,
    ) -> None:
        self.portfolios: Dict[str, Portfolio] = {
            code: Portfolio(
                market_code=code,
                cash=initial_capital,
                max_positions=max_positions,
                max_single_position=max_single_position,
            )
            for code in market_codes
        }
        self.daily_loss_limit = daily_loss_limit
        self.max_drawdown_limit = max_drawdown_limit
        self.current_date: Optional[str] = None


def build_from_config(
    markets_cfg: Dict[str, Any],
    backtest_cfg: Dict[str, Any],
    risk_cfg: Dict[str, Any],
) -> MultiPortfolioManager:
    market_codes = [m.get("code") for m in markets_cfg.get("markets", []) if m.get("code")]
    core = backtest_cfg.get("backtest", {})
    risk = risk_cfg.get("risk", {})

    return MultiPortfolioManager(
        market_codes=market_codes,
        initial_capital=float(core.get("initial_capital", 100000)),
        max_positions=int(core.get("max_positions", 10)),
        max_single_position=float(core.get("max_single_position", 0.2)),
        daily_loss_limit=float(risk.get("daily_loss_limit", 0.05)),
        max_drawdown_limit=float(risk.get("max_drawdown", 0.15)),
    )

    def start_day(self, date: str) -> None:
        if self.current_date != date:
            self.current_date = date
            for portfolio in self.portfolios.values():
                portfolio.start_day()

    def step(
        self,
        date: str,
        prices_by_market: Dict[str, Dict[str, float]],
        target_weights_by_market: Dict[str, Dict[str, float]],
    ) -> Dict[str, PortfolioResult]:
        self.start_day(date)
        results: Dict[str, PortfolioResult] = {}

        for code, portfolio in self.portfolios.items():
            prices = prices_by_market.get(code, {})
            portfolio.update_equity(prices)

            halt_reason: Optional[str] = None
            if check_daily_loss(
                portfolio.equity,
                portfolio.daily_start_equity,
                self.daily_loss_limit,
            ):
                portfolio.trading_halted = True
                halt_reason = "daily_loss_limit"

            if check_max_drawdown_current(
                portfolio.equity,
                portfolio.peak_equity,
                self.max_drawdown_limit,
            ):
                portfolio.trading_halted = True
                halt_reason = halt_reason or "max_drawdown"

            trades: List[Trade]
            if portfolio.trading_halted:
                trades = portfolio.liquidate(prices)
            else:
                targets = target_weights_by_market.get(code, {})
                trades = portfolio.rebalance(targets, prices)

            results[code] = PortfolioResult(
                market_code=code,
                equity=portfolio.equity,
                trades=trades,
                halted=portfolio.trading_halted,
                halt_reason=halt_reason,
            )

        return results
