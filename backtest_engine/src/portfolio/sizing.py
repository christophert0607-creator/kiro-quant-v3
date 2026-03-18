from __future__ import annotations


def kelly_fraction(win_rate: float, win_loss_ratio: float, fraction: float = 0.25) -> float:
    if win_loss_ratio <= 0:
        return 0.0
    raw = win_rate - (1.0 - win_rate) / win_loss_ratio
    return max(raw, 0.0) * fraction