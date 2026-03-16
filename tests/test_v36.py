#!/usr/bin/env python3
"""
V3.6 單元測試套件
測試 Kelly、交易成本、風險度量、績效評估及回測驗證等功能。
"""

import unittest
import numpy as np
from v36 import (
    KellyPositionSizer,
    TransactionCostCalculator,
    RiskMetrics,
    PerformanceMetrics,
    BacktestValidator,
    MarketRegimeDetector
)

class TestV36Modules(unittest.TestCase):
    
    def test_kelly_position_sizer(self):
        sizer = KellyPositionSizer()
        
        # Win rate: 55%, Avg Win: 100, Avg Loss: 80, Portfolio: 100000
        # b = 100 / 80 = 1.25
        # p = 0.55, q = 0.45
        # Kelly raw = (1.25 * 0.55 - 0.45) / 1.25 = (0.6875 - 0.45) / 1.25 = 0.2375 / 1.25 = 0.19
        
        kelly_raw = sizer.calculate_kelly(win_rate=0.55, avg_win=100.0, avg_loss=80.0)
        self.assertAlmostEqual(kelly_raw, 0.19, places=4)
        
        result = sizer.calculate_position_size(
            win_rate=0.55,
            avg_win=100.0,
            avg_loss=80.0,
            portfolio_value=100000.0,
            tier="verified"
        )
        # verified tier: 50% kelly_pct
        # kelly_factor: 0.5 (half kelly)
        # adjusted = 0.19 * 0.50 * 0.5 = 0.0475
        
        self.assertEqual(result["reason"], "ok")
        self.assertAlmostEqual(result["kelly_adjusted"], 0.0475, places=4)
        self.assertAlmostEqual(result["position_value"], 4750.0, places=2)

    def test_transaction_cost(self):
        calc = TransactionCostCalculator()
        
        # 100 shares @ $150
        cost = calc.calculate_total_cost("US.AAPL", 100, 150.0, "large_cap", "BUY")
        trade_value = 15000.0
        
        # US Stock Commission: 0.1% -> 15.0
        # Slippage large cap: 0.05% -> 7.5
        # Total cost: 22.5
        
        self.assertAlmostEqual(cost["trade_value"], 15000.0)
        self.assertAlmostEqual(cost["total_cost"], 22.5)
        self.assertAlmostEqual(cost["cost_rate"], 0.0015, places=4)

    def test_risk_metrics(self):
        risk = RiskMetrics()
        np.random.seed(42)
        returns = np.random.normal(0.001, 0.02, 1000).tolist()
        
        var_res = risk.calculate_var(returns, portfolio_value=100000, confidence_level=0.95)
        self.assertGreater(var_res["var_dollar"], 0)
        self.assertTrue("historical" in var_res["method"])
        
        cvar_res = risk.calculate_cvar(returns, portfolio_value=100000, confidence_level=0.95)
        self.assertGreater(cvar_res["cvar_dollar"], var_res["var_dollar"])
        
        # Test max drawdown
        equity = [100, 110, 90, 80, 120]
        mdd_res = risk.calculate_max_drawdown(equity)
        # 峰值 110, 谷底 80
        # DD = (80 - 110) / 110 = -30 / 110 = -0.2727
        self.assertAlmostEqual(mdd_res["max_drawdown"], -0.272727, places=4)
        self.assertEqual(mdd_res["peak_idx"], 1)
        self.assertEqual(mdd_res["trough_idx"], 3)

    def test_performance_metrics(self):
        perf = PerformanceMetrics()
        
        returns = [0.01, -0.005, 0.02, -0.01, 0.015, 0.03, -0.02]
        metrics = perf.calculate_all_metrics(returns)
        
        self.assertTrue("sharpe_ratio" in metrics)
        self.assertTrue("calmar_ratio" in metrics)
        self.assertTrue("sortino_ratio" in metrics)
        
        # Win rate should be 4 / 7 = 0.5714
        self.assertAlmostEqual(metrics["win_rate"], 0.5714, places=3)
        
    def test_backtest_validator(self):
        signals = [{"timestamp": 1}, {"timestamp": 2}, {"timestamp": 3}]
        prices = [{"timestamp": 1}, {"timestamp": 2}, {"timestamp": 3}]
        
        res = BacktestValidator.check_look_ahead_bias(signals, prices)
        self.assertTrue(res["passed"])
        
        # Test violation: signal at T=2 using price at T=1?
        # Actually violation is signal > price time.
        signals_bad = [{"timestamp": 1}, {"timestamp": 3}, {"timestamp": 4}]
        res_bad = BacktestValidator.check_look_ahead_bias(signals_bad, prices)
        self.assertFalse(res_bad["passed"])
        
    def test_market_regime(self):
        detector = MarketRegimeDetector()
        
        # Trending up
        prices_up = np.linspace(100, 200, 30).tolist()
        res_up = detector.detect_regime(prices_up, lookback=20)
        self.assertEqual(res_up["regime"], "trending_up")
        
        # Sideways (oscillation)
        prices_side = [100 + 5 * np.sin(i) for i in range(30)]
        res_side = detector.detect_regime(prices_side, lookback=20)
        self.assertEqual(res_side["regime"], "sideways")
        
if __name__ == '__main__':
    unittest.main()
