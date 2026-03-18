from __future__ import annotations

from typing import Dict


def enforce_constraints(
    target_weights: Dict[str, float],
    max_positions: int,
    max_single_position: float,
) -> Dict[str, float]:
    # Remove zero weights
    items = [(sym, w) for sym, w in target_weights.items() if abs(w) > 0]

    # Keep top-N by absolute weight
    items.sort(key=lambda x: abs(x[1]), reverse=True)
    items = items[:max_positions]

    constrained: Dict[str, float] = {}
    for sym, weight in items:
        capped = max(min(weight, max_single_position), -max_single_position)
        constrained[sym] = capped

    total_abs = sum(abs(w) for w in constrained.values())
    if total_abs > 1.0:
        scale = 1.0 / total_abs
        constrained = {sym: w * scale for sym, w in constrained.items()}

    return constrained
