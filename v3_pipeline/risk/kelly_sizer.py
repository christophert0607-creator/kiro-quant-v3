from dataclasses import dataclass


@dataclass
class KellySizingConfig:
    kelly_factor: float = 0.5
    max_fraction: float = 0.20


class KellyPositionSizer:
    """Position sizing via Kelly formula with optional fractional Kelly scaling."""

    def __init__(self, config: KellySizingConfig | None = None) -> None:
        self.config = config or KellySizingConfig()

    def calculate_fraction(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        return self.calculate_details(win_rate, avg_win, avg_loss)["capped_fraction"]

    def calculate_details(self, win_rate: float, avg_win: float, avg_loss: float) -> dict:
        """Return full Kelly sizing breakdown for structured event emission.

        Keys: kelly_raw, kelly_scaled, capped_fraction, zero_edge (bool).
        """
        if avg_win <= 0 or avg_loss <= 0:
            return {"kelly_raw": 0.0, "kelly_scaled": 0.0, "capped_fraction": 0.0, "zero_edge": True}

        p = min(max(float(win_rate), 0.0), 1.0)
        q = 1.0 - p
        b = float(avg_win) / float(avg_loss)
        if b <= 0:
            return {"kelly_raw": 0.0, "kelly_scaled": 0.0, "capped_fraction": 0.0, "zero_edge": True}

        kelly_raw = (b * p - q) / b
        kelly_scaled = kelly_raw * float(self.config.kelly_factor)
        capped = max(0.0, min(kelly_scaled, float(self.config.max_fraction)))
        return {
            "kelly_raw": round(kelly_raw, 6),
            "kelly_scaled": round(kelly_scaled, 6),
            "capped_fraction": round(capped, 6),
            "zero_edge": capped <= 0.0,
        }

    def calculate_position_value(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        portfolio_value: float,
    ) -> float:
        if portfolio_value <= 0:
            return 0.0
        fraction = self.calculate_fraction(win_rate=win_rate, avg_win=avg_win, avg_loss=avg_loss)
        return float(portfolio_value) * fraction
