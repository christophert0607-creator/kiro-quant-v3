from dataclasses import dataclass

import v3_launcher as launcher


@dataclass
class DummyLiveConfig:
    symbol: str = "TSLA"
    max_positions: int = 5


def test_filter_live_config_kwargs_drops_unknown_fields() -> None:
    raw_cfg = {
        "symbol": "NVDA",
        "max_positions": 3,
        "ruin_threshold": 0.15,
    }

    filtered = launcher._filter_live_config_kwargs(DummyLiveConfig, raw_cfg)

    assert filtered == {
        "symbol": "NVDA",
        "max_positions": 3,
    }
    assert "ruin_threshold" not in filtered
