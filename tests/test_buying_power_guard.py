"""Tests for BuyingPowerGuard."""

import pytest
import time
from unittest.mock import MagicMock

import sys
sys.path.insert(0, "v3_pipeline/core")

from buying_power_guard import BuyingPowerGuard


class TestBuyingPowerGuard:
    """Unit tests for the buying power preflight guard."""

    def test_sell_side_no_check(self):
        guard = BuyingPowerGuard()
        can, reason = guard.can_execute("AAPL", 100, 150.0, side="SELL")
        assert can is True
        assert reason == "sell_side_no_check"

    def test_qty_must_be_positive(self):
        guard = BuyingPowerGuard()
        can, reason = guard.can_execute("AAPL", 0, 150.0, side="BUY")
        assert can is False
        assert reason == "qty_must_be_positive"

    def test_est_cost_zero(self):
        guard = BuyingPowerGuard()
        can, reason = guard.can_execute("AAPL", 100, 0.0, side="BUY")
        assert can is False
        assert reason == "est_cost_zero"

    def test_insufficient_cash(self):
        connector = MagicMock()
        connector.get_sync_assets.return_value = {
            "cash": 500.0,
            "power": 500.0,
        }
        guard = BuyingPowerGuard(connector)
        can, reason = guard.can_execute("AAPL", 100, 150.0, side="BUY")
        assert can is False
        assert "effective_bp" in reason or "effective_bp < est_cost" in reason

    def test_sufficient_max_buying_power(self):
        connector = MagicMock()
        connector.get_sync_assets.return_value = {
            "cash": 500.0,
            "power": 500.0,
        }
        # Mock _fetch_buying_power to return max_buying_power
        guard = BuyingPowerGuard(connector)
        # Patch _fetch_buying_power directly
        original_fetch = guard._fetch_buying_power
        guard._fetch_buying_power = lambda market="US": {
            "cash": 500.0,
            "available_funds": 500.0,
            "power": 500.0,
            "max_buying_power": 20000.0,
        }
        can, reason = guard.can_execute("AAPL", 100, 150.0, side="BUY")
        assert can is True
        assert reason == "ok"

    def test_caching_insufficient(self):
        connector = MagicMock()
        connector.get_sync_assets.return_value = {
            "cash": 500.0,
            "power": 500.0,
        }
        guard = BuyingPowerGuard(connector)
        # Patch _fetch_buying_power to track calls
        call_count = [0]
        def mock_fetch(market="US"):
            call_count[0] += 1
            return {
                "cash": 500.0,
                "available_funds": 500.0,
                "power": 500.0,
                "max_buying_power": 500.0,
            }
        guard._fetch_buying_power = mock_fetch
        
        # First call hits API
        can1, _ = guard.can_execute("AAPL", 100, 150.0, side="BUY")
        assert can1 is False
        # Second call uses cache
        can2, _ = guard.can_execute("AAPL", 100, 150.0, side="BUY")
        assert can2 is False
        assert call_count[0] == 1

    def test_cache_expiry(self):
        connector = MagicMock()
        guard = BuyingPowerGuard(connector)
        guard.INSUFFICIENT_CACHE_TTL = 0.01  # 10ms for test
        
        call_count = [0]
        def mock_fetch(market="US"):
            call_count[0] += 1
            return {
                "cash": 500.0,
                "available_funds": 500.0,
                "power": 500.0,
                "max_buying_power": 500.0,
            }
        guard._fetch_buying_power = mock_fetch
        
        can1, _ = guard.can_execute("AAPL", 100, 150.0, side="BUY")
        assert can1 is False
        time.sleep(0.02)  # wait for cache expiry
        can2, _ = guard.can_execute("AAPL", 100, 150.0, side="BUY")
        assert can2 is False
        assert call_count[0] == 2

    def test_per_market_sync(self):
        connector = MagicMock()
        connector.get_sync_assets_for_market.return_value = {
            "cash": 1000.0,
            "power": 1000.0,
        }
        guard = BuyingPowerGuard(connector)
        # Patch to use per-market
        def mock_fetch(market="US"):
            if market == "HK":
                return {"cash": 1000.0, "available_funds": 1000.0, "power": 1000.0, "max_buying_power": 1000.0}
            return {"cash": 50000.0, "available_funds": 50000.0, "power": 50000.0, "max_buying_power": 50000.0}
        guard._fetch_buying_power = mock_fetch
        
        can, reason = guard.can_execute("0700.HK", 100, 50.0, side="BUY", market="HK")
        assert can is False

    def test_invalidate_cache(self):
        connector = MagicMock()
        guard = BuyingPowerGuard(connector)
        guard._fetch_buying_power = lambda market="US": {
            "cash": 500.0, "available_funds": 500.0, "power": 500.0, "max_buying_power": 500.0,
        }
        guard.can_execute("AAPL", 100, 150.0, side="BUY")
        assert len(guard._cache) == 1
        guard.invalidate_cache("AAPL")
        assert len(guard._cache) == 0

    def test_get_stats(self):
        connector = MagicMock()
        guard = BuyingPowerGuard(connector)
        guard._fetch_buying_power = lambda market="US": {
            "cash": 500.0, "available_funds": 500.0, "power": 500.0, "max_buying_power": 500.0,
        }
        guard.can_execute("AAPL", 100, 150.0, side="BUY")
        stats = guard.get_stats()
        assert stats["cached_symbols"] == 1
        assert stats["insufficient_symbols"] == 1

    def test_noise_suppression(self):
        connector = MagicMock()
        guard = BuyingPowerGuard(connector)
        guard.INSUFFICIENT_CACHE_TTL = 3600  # long cache for test
        guard._fetch_buying_power = lambda market="US": {
            "cash": 500.0, "available_funds": 500.0, "power": 500.0, "max_buying_power": 500.0,
        }
        # Call 4 times — only first 3 should log
        for _ in range(4):
            guard.can_execute("AAPL", 100, 150.0, side="BUY")
        assert guard._insufficient_count["AAPL"] == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
