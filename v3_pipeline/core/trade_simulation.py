import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class PositionState:
    qty: int = 0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0
    entry_time: Optional[str] = None
    last_fill_time: Optional[str] = None


@dataclass
class PnLTracker:
    out_path: Path = Path("pnl_report.json")
    positions: dict[str, PositionState] = field(default_factory=dict)

    def record_fill(self, symbol: str, side: str, qty: int, price: float, fill_time: Optional[datetime] = None) -> None:
        if qty <= 0:
            return
        ts = (fill_time or datetime.now(timezone.utc)).isoformat()
        state = self.positions.setdefault(symbol, PositionState())
        side_upper = side.upper()

        if side_upper == "BUY":
            total_cost = state.avg_cost * state.qty + float(price) * qty
            state.qty += qty
            state.avg_cost = total_cost / max(1, state.qty)
            state.entry_time = state.entry_time or ts
        else:
            close_qty = min(state.qty, qty)
            state.realized_pnl += (float(price) - state.avg_cost) * close_qty
            state.qty = max(0, state.qty - qty)
            if state.qty == 0:
                state.avg_cost = 0.0
                state.entry_time = None
        state.last_fill_time = ts

    def build_report(self, latest_prices: dict[str, float]) -> dict:
        symbols: dict[str, dict] = {}
        total_unrealized = 0.0
        total_realized = 0.0

        for symbol, state in self.positions.items():
            mark = float(latest_prices.get(symbol, state.avg_cost))
            unrealized = (mark - state.avg_cost) * state.qty
            total_unrealized += unrealized
            total_realized += state.realized_pnl
            symbols[symbol] = {
                "qty": state.qty,
                "avg_cost": state.avg_cost,
                "cost_basis": state.avg_cost * state.qty,
                "entry_time": state.entry_time,
                "last_fill_time": state.last_fill_time,
                "mark_price": mark,
                "unrealized_pnl": unrealized,
                "realized_pnl": state.realized_pnl,
            }

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_unrealized_pnl": total_unrealized,
            "total_realized_pnl": total_realized,
            "net_pnl": total_unrealized + total_realized,
            "symbols": symbols,
        }
        self.out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


@dataclass
class PaperTradingSimulator:
    starting_cash: float = 100000.0
    pnl_path: Path = Path("paper_trading_pnl.json")
    slippage_bps: float = 5.0
    tracker: PnLTracker = field(default_factory=PnLTracker)
    cash: float = field(init=False)

    def __post_init__(self) -> None:
        self.cash = float(self.starting_cash)

    def _simulate_fill_price(self, side: str, reference_price: float) -> float:
        slip = max(0.0, self.slippage_bps) / 10000.0
        if side.upper() == "BUY":
            return float(reference_price) * (1.0 + slip)
        return float(reference_price) * (1.0 - slip)

    def execute_order(self, symbol: str, side: str, qty: int, reference_price: float) -> dict:
        if qty <= 0:
            raise ValueError("qty must be > 0")
        fill_price = self._simulate_fill_price(side, reference_price)
        notion = fill_price * qty
        if side.upper() == "BUY":
            if self.cash < notion:
                raise RuntimeError(f"paper trading cash insufficient: cash={self.cash:.2f} need={notion:.2f}")
            self.cash -= notion
        else:
            self.cash += notion

        self.tracker.record_fill(symbol=symbol, side=side, qty=qty, price=fill_price)
        return {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "fill_price": fill_price,
            "notional": notion,
            "cash": self.cash,
        }

    def mark_to_market(self, latest_prices: dict[str, float]) -> dict:
        pnl = self.tracker.build_report(latest_prices)
        equity = self.cash
        for symbol, info in pnl.get("symbols", {}).items():
            equity += int(info.get("qty", 0)) * float(latest_prices.get(symbol, info.get("mark_price", 0.0)))
        output = {
            "timestamp": pnl.get("timestamp"),
            "cash": self.cash,
            "equity": equity,
            "realized_pnl": pnl.get("total_realized_pnl", 0.0),
            "unrealized_pnl": pnl.get("total_unrealized_pnl", 0.0),
            "net_pnl": pnl.get("net_pnl", 0.0),
            "positions": pnl.get("symbols", {}),
        }
        self.pnl_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        return output
