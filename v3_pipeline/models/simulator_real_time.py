#!/usr/bin/env python3
"""
Real-Time Trading Simulator
==========================
全面的實時交易模擬系統，包括：
1. Walk-Forward Analysis - 滾動向前分析
2. Monte Carlo Simulation - 蒙特卡羅隨機模擬
3. Stress Testing - 壓力測試 (模擬歷史股災)

Author: Kiro Quant V3
"""

import argparse
import json
import logging
import sys
import random
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd
import yfinance as yf

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("TradingSimulator")


# ============ 配置 ============

@dataclass
class SimulatorConfig:
    initial_capital: float = 100000  # 初始資金
    commission_rate: float = 0.001   # 佣金 0.1%
    slippage: float = 0.0005         # 滑點 0.05%
    max_position_pct: float = 0.2    # 最大倉位 20%
    stop_loss_pct: float = 0.02      # 止損 2%
    take_profit_pct: float = 0.03    # 止盈 3%


@dataclass
class Trade:
    timestamp: str
    symbol: str
    action: str  # BUY/SELL
    price: float
    quantity: int
    pnl: float = 0.0


@dataclass
class Portfolio:
    cash: float
    positions: dict = field(default_factory=dict)  # {symbol: quantity}
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    
    def total_value(self, prices: dict) -> float:
        pos_value = sum(qty * prices.get(sym, 0) for sym, qty in self.positions.items())
        return self.cash + pos_value


# ============ 策略信號 ============

def generate_signal(df: pd.DataFrame) -> int:
    """簡單的交易信號生成"""
    if len(df) < 30:
        return 0
    
    close = df['Close'].values
    
    # 簡單移動平均線交叉
    sma5 = np.mean(close[-5:])
    sma20 = np.mean(close[-20:])
    
    # 簡單RSI
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-14:]) if len(gains) >= 14 else 0
    avg_loss = np.mean(losses[-14:]) if len(losses) >= 14 else 0
    
    if avg_loss == 0:
        rsi_val = 100
    else:
        rs = avg_gain / avg_loss
        rsi_val = 100 - (100 / (1 + rs))
    
    # 信號邏輯
    if rsi_val < 35 and sma5 > sma20:
        return 1  # BUY
    elif rsi_val > 65 and sma5 < sma20:
        return -1  # SELL
    return 0  # HOLD


# ============ 模擬引擎 ============

class TradingSimulator:
    """交易模擬器"""
    
    def __init__(self, config: SimulatorConfig):
        self.config = config
        
    def run_walk_forward(self, symbol: str, years: int = 3) -> dict:
        """Walk-Forward Analysis"""
        logger.info(f"\n{'='*60}")
        logger.info(f"📊 Walk-Forward Analysis: {symbol}")
        logger.info(f"{'='*60}")
        
        # 獲取數據
        df = yf.download(symbol, period=f"{years}y", progress=False)
        
        if df.empty:
            logger.error(f"No data for {symbol}")
            return {}
        
        # 參數
        train_days = 252   # 訓練期 1年
        test_days = 21     # 測試期 1個月
        total_days = len(df)
        
        results = []
        
        # Walk-Forward 滾動
        start_idx = train_days
        while start_idx + test_days <= total_days:
            # 訓練期數據
            train_df = df.iloc[start_idx - train_days:start_idx]
            # 測試期數據
            test_df = df.iloc[start_idx:start_idx + test_days]
            
            # 模擬交易
            portfolio = Portfolio(cash=self.config.initial_capital)
            prices = {}
            
            for idx, row in test_df.iterrows():
                date = idx.strftime("%Y-%m-%d")
                price = row['Close']
                prices[symbol] = price
                
                # 記錄 equity
                portfolio.equity_curve.append({
                    'date': date,
                    'value': portfolio.total_value(prices)
                })
                
                # 生成信號 (使用截至當前的數據)
                signal_data = df.loc[:idx].tail(30)
                signal = generate_signal(signal_data)
                
                # 執行交易
                if signal == 1 and portfolio.cash > price * 10:  # 買入
                    qty = int(portfolio.cash * 0.1 / price)  # 10% 資金
                    cost = qty * price * (1 + self.config.commission_rate + self.config.slippage)
                    if cost <= portfolio.cash:
                        portfolio.cash -= cost
                        portfolio.positions[symbol] = portfolio.positions.get(symbol, 0) + qty
                        portfolio.trades.append(Trade(date, symbol, "BUY", price, qty))
                        
                elif signal == -1 and portfolio.positions.get(symbol, 0) > 0:  # 賣出
                    qty = portfolio.positions[symbol]
                    revenue = qty * price * (1 - self.config.commission_rate - self.config.slippage)
                    portfolio.cash += revenue
                    portfolio.positions[symbol] = 0
                    portfolio.trades.append(Trade(date, symbol, "SELL", price, qty))
            
            # 計算結果
            final_value = portfolio.total_value(prices)
            total_return = (final_value - self.config.initial_capital) / self.config.initial_capital * 100
            
            results.append({
                'period': f"{test_df.index[0].strftime('%Y-%m-%d')} to {test_df.index[-1].strftime('%Y-%m-%d')}",
                'final_value': final_value,
                'return_pct': total_return,
                'trades': len(portfolio.trades)
            })
            
            start_idx += test_days
        
        # 總結
        returns = [r['return_pct'] for r in results]
        avg_return = np.mean(returns)
        win_rate = len([r for r in returns if r > 0]) / len(returns) * 100
        
        logger.info(f"\n📈 Walk-Forward 結果:")
        logger.info(f"  總週期數: {len(results)}")
        logger.info(f"  平均回報: {avg_return:.2f}%")
        logger.info(f"  勝率: {win_rate:.1f}%")
        logger.info(f"  最佳: {max(returns):.2f}%")
        logger.info(f"  最差: {min(returns):.2f}%")
        
        return {
            'type': 'walk_forward',
            'symbol': symbol,
            'periods': results,
            'avg_return': avg_return,
            'win_rate': win_rate
        }
    
    def run_monte_carlo(self, symbol: str, n_sims: int = 1000) -> dict:
        """Monte Carlo Simulation"""
        logger.info(f"\n{'='*60}")
        logger.info(f"🎲 Monte Carlo Simulation: {symbol} ({n_sims} simulations)")
        logger.info(f"{'='*60}")
        
        # 獲取數據
        df = yf.download(symbol, period="2y", progress=False)
        
        # Flatten if multi-dimensional
        close_prices = df['Close'].values.flatten() if df['Close'].values.ndim > 1 else df['Close'].values
        returns = np.diff(close_prices) / close_prices[:-1]
        
        if len(returns) < 30:
            logger.error("Insufficient data")
            return {}
        
        # 模擬
        sim_results = []
        
        for sim in range(n_sims):
            portfolio = float(self.config.initial_capital)
            
            # 隨機抽樣 21交易日 (1個月)
            sampled = np.random.choice(returns, size=21, replace=True)
            
            for ret in sampled:
                portfolio *= (1 + ret)
            
            sim_results.append(portfolio)
        
        sim_results = np.array(sim_results)
        
        # 統計
        median = np.median(sim_results)
        percentile_5 = np.percentile(sim_results, 5)
        percentile_95 = np.percentile(sim_results, 95)
        prob_loss = (sim_results < self.config.initial_capital).mean() * 100
        prob_10pct = (sim_results > self.config.initial_capital * 1.1).mean() * 100
        
        logger.info(f"\n📊 Monte Carlo 結果 (21交易日):")
        logger.info(f"  初始資金: ${self.config.initial_capital:,.0f}")
        logger.info(f"  中位數結果: ${median:,.0f} ({(median/self.config.initial_capital-1)*100:+.1f}%)")
        logger.info(f"  5%分位 (最差): ${percentile_5:,.0f} ({(percentile_5/self.config.initial_capital-1)*100:+.1f}%)")
        logger.info(f"  95%分位 (最好): ${percentile_95:,.0f} ({(percentile_95/self.config.initial_capital-1)*100:+.1f}%)")
        logger.info(f"  虧損概率: {prob_loss:.1f}%")
        logger.info(f"  賺10%概率: {prob_10pct:.1f}%")
        
        return {
            'type': 'monte_carlo',
            'symbol': symbol,
            'n_simulations': n_sims,
            'initial_capital': self.config.initial_capital,
            'median_result': median,
            'percentile_5': percentile_5,
            'percentile_95': percentile_95,
            'prob_loss': prob_loss,
            'prob_10pct_gain': prob_10pct
        }
    
    def run_stress_test(self, symbol: str) -> dict:
        """Stress Testing - 模擬歷史股災"""
        logger.info(f"\n{'='*60}")
        logger.info(f"⚠️ Stress Testing: {symbol}")
        logger.info(f"{'='*60}")
        
        # 獲取數據
        df = yf.download(symbol, period="5y", progress=False)
        
        # 模擬不同場景
        scenarios = {
            '正常市場': np.random.normal(0.001, 0.02, 21),  # 每日平均漲0.1%
            '2020新冠': np.random.normal(-0.03, 0.05, 21),   # 平均-3%一天, 高波動
            '2008金融海嘯': np.random.normal(-0.02, 0.08, 21),
            '閃崩': np.random.normal(-0.10, 0.15, 5),       # 5日暴跌
            '強勁反彈': np.random.normal(0.02, 0.03, 21),   # 強勁反彈
        }
        
        results = {}
        
        for scenario_name, returns in scenarios.items():
            portfolio = self.config.initial_capital
            
            for ret in returns:
                portfolio *= (1 + ret)
            
            pnl = portfolio - self.config.initial_capital
            pnl_pct = pnl / self.config.initial_capital * 100
            
            results[scenario_name] = {
                'final_value': portfolio,
                'pnl': pnl,
                'pnl_pct': pnl_pct
            }
            
            logger.info(f"\n  {scenario_name}:")
            logger.info(f"    最終價值: ${portfolio:,.0f}")
            logger.info(f"    損益: ${pnl:,.0f} ({pnl_pct:+.1f}%)")
        
        return {
            'type': 'stress_test',
            'symbol': symbol,
            'scenarios': results
        }


# ============ 主程序 ============

def main():
    parser = argparse.ArgumentParser(description="Real-Time Trading Simulator")
    parser.add_argument("--symbol", default="NVDA", help="Stock symbol")
    parser.add_argument("--capital", type=float, default=100000, help="Initial capital")
    parser.add_argument("--n-sims", type=int, default=1000, help="Monte Carlo simulations")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    args = parser.parse_args()
    
    config = SimulatorConfig(initial_capital=args.capital)
    simulator = TradingSimulator(config)
    
    logger.info("="*60)
    logger.info("🚀 Kiro Quant V3 - Real-Time Trading Simulator")
    logger.info("="*60)
    logger.info(f"Symbol: {args.symbol}")
    logger.info(f"Initial Capital: ${args.capital:,.0f}")
    
    all_results = {}
    
    # 1. Walk-Forward Analysis
    if args.all or True:
        all_results['walk_forward'] = simulator.run_walk_forward(args.symbol, years=3)
    
    # 2. Monte Carlo Simulation
    if args.all or True:
        all_results['monte_carlo'] = simulator.run_monte_carlo(args.symbol, args.n_sims)
    
    # 3. Stress Testing
    if args.all or True:
        all_results['stress_test'] = simulator.run_stress_test(args.symbol)
    
    # 保存結果
    results_file = f"v3_pipeline/reports/simulator_results_{args.symbol}_{datetime.now().strftime('%Y%m%d')}.json"
    Path(results_file).parent.mkdir(parents=True, exist_ok=True)
    with open(results_file, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"✅ 模擬完成！結果已保存到: {results_file}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
