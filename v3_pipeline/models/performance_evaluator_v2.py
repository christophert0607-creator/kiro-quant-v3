#!/usr/bin/env python3
"""
Performance Evaluator V2
====================
完整績效評估系統：
- Sharpe Ratio, Profit Factor, Max Drawdown, Win Rate, Calmar Ratio
- Equity Curve + Drawdown Chart
- 分 Regime 顯示績效
- 支援 Confidence Threshold 回測

Author: Kiro Quant V3
"""

import argparse
import json
import logging
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler
import joblib

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("PerformanceEvaluator")


# ============ Metrics ============

def calculate_sharpe(returns: np.ndarray, risk_free_rate: float = 0.04) -> float:
    """計算年化 Sharpe Ratio"""
    if len(returns) == 0 or np.std(returns) == 0:
        return 0.0
    
    excess_returns = returns - risk_free_rate / 252
    return np.mean(excess_returns) / np.std(returns) * np.sqrt(252)


def calculate_max_drawdown(equity_curve: np.ndarray) -> dict:
    """計算最大回撤"""
    equity = np.array(equity_curve)
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max
    
    max_dd = np.min(drawdown)
    peak_idx = np.argmax(running_max)
    trough_idx = np.argmin(drawdown)
    
    return {
        'max_drawdown': abs(max_dd),
        'peak_value': running_max[peak_idx],
        'trough_value': equity[trough_idx],
        'recovery_time': None  # Simplified
    }


def calculate_profit_factor(trades: list) -> float:
    """計算 Profit Factor"""
    # Filter only SELL trades with profit
    profits = [t['profit'] for t in trades if 'profit' in t and t['profit'] > 0]
    losses = [abs(t['profit']) for t in trades if 'profit' in t and t['profit'] < 0]
    
    total_profit = sum(profits) if profits else 0
    total_loss = sum(losses) if losses else 0
    
    if total_loss == 0:
        return 0.0 if total_profit == 0 else float('inf')
    
    return total_profit / total_loss


def calculate_calmar(returns: np.ndarray, max_drawdown: float) -> float:
    """計算 Calmar Ratio"""
    if max_drawdown == 0:
        return 0.0
    
    annual_return = np.mean(returns) * 252
    return annual_return / max_drawdown


# ============ Regime Detection ============

def detect_regime(close: np.ndarray, lookback: int = 20) -> str:
    """檢測市場環境"""
    if len(close) < lookback * 2:
        return "UNKNOWN"
    
    recent = np.mean(close[-lookback:])
    early = np.mean(close[:lookback])
    trend = (recent - early) / early
    
    returns = np.diff(close) / close[:-1]
    vol_now = np.std(returns[-lookback:])
    vol_hist = np.std(returns)
    vol_ratio = vol_now / vol_hist if vol_hist > 0 else 1.0
    
    if vol_ratio > 1.5:
        return "HIGH_VOL"
    elif vol_ratio < 0.7:
        return "LOW_VOL"
    elif trend > 0.08:
        return "BULL"
    elif trend < -0.08:
        return "BEAR"
    else:
        return "SIDEWAYS"


# ============ Trading Simulation ============

def simulate_trading(prices: np.ndarray, predictions: np.ndarray, 
                    confidence_threshold: float = 0.65,
                    initial_capital: float = 100000,
                    commission: float = 0.001) -> dict:
    """模擬交易"""
    
    capital = initial_capital
    position = 0
    trades = []
    equity_curve = [initial_capital]
    regime_history = []
    
    for i in range(len(predictions) - 1):
        if i >= len(prices) - 1:
            break
        
        # Detect regime
        window = prices[max(0, i-50):i+1]
        regime = detect_regime(window)
        regime_history.append(regime)
        
        price = prices[i]
        pred = predictions[i]
        
        # Apply confidence threshold
        action = 1  # Hold by default
        
        # Simplified: treat prediction as confidence
        if pred >= 2 - confidence_threshold:  # BUY
            if pred >= 2 - (1 - confidence_threshold):
                action = 2  # Strong BUY
        elif pred <= confidence_threshold:  # SELL
            if pred <= 1 - (1 - confidence_threshold):
                action = 0  # Strong SELL
        
        # Execute trades
        if action == 2 and position == 0:  # Buy
            cost = capital * 0.95  # Use 95% of capital
            position = cost / price
            capital = 0
            trades.append({
                'type': 'BUY',
                'price': price,
                'regime': regime,
                'day': i
            })
        
        elif action == 0 and position > 0:  # Sell
            revenue = position * price * (1 - commission)
            profit = revenue - (trades[-1]['price'] * position)
            trades.append({
                'type': 'SELL',
                'price': price,
                'profit': profit,
                'regime': regime,
                'day': i
            })
            capital = revenue
            position = 0
        
        # Record equity
        if position > 0:
            current_value = position * price
        else:
            current_value = capital
        equity_curve.append(current_value)
    
    # Close final position
    if position > 0:
        final_price = prices[-1]
        revenue = position * final_price * (1 - commission)
        trades.append({
            'type': 'SELL',
            'price': final_price,
            'profit': revenue - (trades[-1]['price'] * position if trades else 0),
            'regime': regime_history[-1] if regime_history else 'UNKNOWN',
            'day': len(predictions)
        })
        capital = revenue
        position = 0
    
    # Calculate returns
    equity_array = np.array(equity_curve)
    returns = np.diff(equity_array) / equity_array[:-1]
    
    # Calculate metrics
    total_return = (equity_array[-1] - initial_capital) / initial_capital
    
    wins = [t for t in trades if t.get('profit', 0) > 0]
    losses = [t for t in trades if t.get('profit', 0) < 0]
    win_rate = len(wins) / (len(wins) + len(losses)) * 100 if (wins + losses) else 0
    
    sharpe = calculate_sharpe(returns)
    profit_factor = calculate_profit_factor(trades)
    max_dd_info = calculate_max_drawdown(equity_array)
    calmar = calculate_calmar(returns, max_dd_info['max_drawdown'])
    
    # Returns by regime
    regime_returns = {}
    regime_trades = {'BUY': [], 'SELL': []}
    for t in trades:
        reg = t.get('regime', 'UNKNOWN')
        if reg not in regime_returns:
            regime_returns[reg] = []
        if 'profit' in t:
            regime_returns[reg].append(t['profit'])
        regime_trades[t['type']].append(reg)
    
    regime_summary = {}
    for reg, profits in regime_returns.items():
        if profits:
            regime_summary[reg] = {
                'avg_profit': np.mean(profits),
                'total_profit': sum(profits),
                'num_trades': len(profits)
            }
    
    return {
        'total_return': total_return * 100,
        'final_value': equity_array[-1],
        'sharpe_ratio': sharpe,
        'profit_factor': profit_factor,
        'max_drawdown': max_dd_info['max_drawdown'] * 100,
        'calmar_ratio': calmar,
        'win_rate': win_rate,
        'num_trades': len(trades),
        'num_wins': len(wins),
        'num_losses': len(losses),
        'equity_curve': equity_curve,
        'regime_summary': regime_summary,
        'regime_trades': {
            'BUY': {reg: regime_trades['BUY'].count(reg) for reg in set(regime_trades['BUY'])},
            'SELL': {reg: regime_trades['SELL'].count(reg) for reg in set(regime_trades['SELL'])}
        }
    }


# ============ Main Evaluation ============

def main():
    parser = argparse.ArgumentParser(description="Performance Evaluator V2")
    parser.add_argument("--symbol", default="NVDA", help="Stock symbol")
    parser.add_argument("--period", default="5y", help="Data period")
    parser.add_argument("--confidence", type=float, default=0.65, help="Confidence threshold")
    parser.add_argument("--initial-capital", type=float, default=100000, help="Initial capital")
    args = parser.parse_args()
    
    logger.info("="*60)
    logger.info("📊 Kiro Quant V3 - Performance Evaluator V2")
    logger.info("="*60)
    logger.info(f"Symbol: {args.symbol}")
    logger.info(f"Confidence Threshold: {args.confidence}")
    logger.info(f"Initial Capital: ${args.initial_capital:,}")
    
    # Load data
    df = yf.download(args.symbol, period=args.period, progress=False)
    if df.empty:
        logger.error("No data!")
        return
    
    prices = df['Close'].values.flatten()
    logger.info(f"Data points: {len(prices)}")
    
    # Generate simple predictions (for demo)
    # In real system, use trained model
    np.random.seed(42)
    predictions = np.random.choice([0, 1, 2], size=len(prices), p=[0.2, 0.5, 0.3])
    
    # Run simulation
    logger.info("\nRunning Trading Simulation...")
    results = simulate_trading(
        prices, 
        predictions,
        confidence_threshold=args.confidence,
        initial_capital=args.initial_capital
    )
    
    # Print results
    logger.info("\n" + "="*60)
    logger.info("📈 完整績效報告")
    logger.info("="*60)
    
    logger.info(f"\n💰 總體表現:")
    logger.info(f"  總回報: {results['total_return']:+.2f}%")
    logger.info(f"  最終價值: ${results['final_value']:,.0f}")
    
    logger.info(f"\n📊 風險調整指標:")
    logger.info(f"  Sharpe Ratio: {results['sharpe_ratio']:.2f}")
    logger.info(f"  Calmar Ratio: {results['calmar_ratio']:.2f}")
    logger.info(f"  Profit Factor: {results['profit_factor']:.2f}")
    
    logger.info(f"\n📉 風險指標:")
    logger.info(f"  最大回撤: {results['max_drawdown']:.2f}%")
    logger.info(f"  交易次數: {results['num_trades']}")
    
    logger.info(f"\n🎯 交易統計:")
    logger.info(f"  勝率: {results['win_rate']:.1f}%")
    logger.info(f"  獲利交易: {results['num_wins']}")
    logger.info(f"  虧損交易: {results['num_losses']}")
    
    if results['regime_summary']:
        logger.info(f"\n📊 分環境績效:")
        for reg, info in results['regime_summary'].items():
            logger.info(f"  {reg}:")
            logger.info(f"    交易次數: {info['num_trades']}")
            logger.info(f"    平均損益: ${info['avg_profit']:,.0f}")
    
    # Grade
    logger.info("\n" + "="*60)
    logger.info("🏆 評分")
    logger.info("="*60)
    
    grade = "F"
    if results['sharpe_ratio'] > 2.0:
        grade = "A+"
    elif results['sharpe_ratio'] > 1.5:
        grade = "A"
    elif results['sharpe_ratio'] > 1.0:
        grade = "B"
    elif results['sharpe_ratio'] > 0.5:
        grade = "C"
    
    if results['max_drawdown'] < 10:
        grade += "+"  # Good risk management
    
    logger.info(f"  總評級: {grade}")
    logger.info(f"  Sharpe > 1.0: {'✅' if results['sharpe_ratio'] > 1.0 else '❌'}")
    logger.info(f"  Max DD < 15%: {'✅' if results['max_drawdown'] < 15 else '❌'}")
    logger.info(f"  Profit Factor > 1.5: {'✅' if results['profit_factor'] > 1.5 else '❌'}")
    
    # Save results
    output_file = f"v3_pipeline/reports/performance_{args.symbol}_{datetime.now().strftime('%Y%m%d')}.json"
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    
    # Remove non-serializable parts
    save_results = {k: v for k, v in results.items() if k != 'equity_curve'}
    
    with open(output_file, 'w') as f:
        json.dump(save_results, f, indent=2, default=str)
    
    logger.info(f"\n✅ Results saved to: {output_file}")


if __name__ == "__main__":
    main()
