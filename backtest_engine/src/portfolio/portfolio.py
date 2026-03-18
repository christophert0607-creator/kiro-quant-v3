from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .constraints import enforce_constraints


@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float

    def market_value(self, price: float) -> float:
        return self.quantity * price


@dataclass
class Trade:
    symbol: str
    quantity: float
    price: float
    value: float


@dataclass
class Portfolio:
    market_code: str
    cash: float
    max_positions: int
    max_single_position: float
    positions: Dict[str, Position] = field(default_factory=dict)
    equity: float = 0.0
    peak_equity: float = 0.0
    daily_start_equity: float = 0.0
    trading_halted: bool = False

    def __post_init__(self) -> None:
        self.equity = self.cash
        self.peak_equity = self.cash
        self.daily_start_equity = self.cash

    def start_day(self) -> None:
        self.daily_start_equity = self.equity

    def update_equity(self, prices: Dict[str, float]) -> float:
        position_value = 0.0
        for sym, pos in self.positions.items():
            price = prices.get(sym, pos.entry_price)
            position_value += pos.market_value(price)
        self.equity = self.cash + position_value
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        return self.equity

    def liquidate(self, prices: Dict[str, float]) -> List[Trade]:
        trades: List[Trade] = []
        for sym, pos in list(self.positions.items()):
            price = prices.get(sym, pos.entry_price)
            value = pos.quantity * price
            self.cash += value
            trades.append(Trade(symbol=sym, quantity=-pos.quantity, price=price, value=-value))
            del self.positions[sym]
        self.update_equity(prices)
        return trades

    def rebalance(self, target_weights: Dict[str, float], prices: Dict[str, float]) -> List[Trade]:
        if self.trading_halted:
            return []

        self.update_equity(prices)
        constrained = enforce_constraints(target_weights, self.max_positions, self.max_single_position)

        target_qty: Dict[str, float] = {}
        for sym, weight in constrained.items():
            price = prices.get(sym)
            if price is None or price <= 0:
                continue
            target_value = self.equity * weight
            target_qty[sym] = target_value / price

        trades: List[Trade] = []
        symbols = set(self.positions.keys()) | set(target_qty.keys())
        for sym in symbols:
            price = prices.get(sym)
            if price is None or price <= 0:
                continue
            current_qty = self.positions.get(sym).quantity if sym in self.positions else 0.0
            desired_qty = target_qty.get(sym, 0.0)
            delta = desired_qty - current_qty
            if abs(delta) < 1e-8:
                continue
            value = delta * price
            self.cash -= value
            if abs(desired_qty) < 1e-8:
                if sym in self.positions:
                    del self.positions[sym]
            else:
                self.positions[sym] = Position(symbol=sym, quantity=desired_qty, entry_price=price)
            trades.append(Trade(symbol=sym, quantity=delta, price=price, value=value))

        self.update_equity(prices)
        return trades