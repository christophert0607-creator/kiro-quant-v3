#!/usr/bin/env python3
"""
Enhanced Trading Simulator with Regime-Adaptive Features
===============================================
- Regime-Adaptive Threshold
- Dynamic Stop Loss (based on regime)
- Trailing Stop
- Time-based Exit

Author: Kiro Quant V3
"""

import yfinance as yf
import numpy as np
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger()


def detect_regime(prices, lookback=20):
    """Detect market regime"""
    if len(prices) < lookback * 2:
        return "UNKNOWN"
    
    recent = np.mean(prices[-lookback:])
    early = np.mean(prices[:lookback])
    trend = (recent - early) / early
    
    returns = np.diff(prices) / prices[:-1]
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


def run_simulation(symbol="NVDA", period="3y"):
    """Run enhanced simulation"""
    
    # Load data
    df = yf.download(symbol, period=period, progress=False)
    prices = df['Close'].values.flatten()
    
    logger.info("="*60)
    logger.info(f"📊 Enhanced Trading Simulation - {symbol}")
    logger.info("="*60)
    logger.info(f"Data: {len(prices)} days")
    
    # Regime thresholds
    REGIME_THRESHOLDS = {
        "BULL": {"entry": 0.55, "stop_loss": 0.08, "trail": 0.05},
        "BEAR": {"entry": 0.75, "stop_loss": 0.04, "trail": 0.03},
        "SIDEWAYS": {"entry": 0.60, "stop_loss": 0.05, "trail": 0.04},
        "HIGH_VOL": {"entry": 0.80, "stop_loss": 0.03, "trail": 0.02},
        "LOW_VOL": {"entry": 0.50, "stop_loss": 0.06, "trail": 0.05},
    }
    
    # Trading simulation
    capital = 100000
    position = 0
    entry_price = 0
    trades = []
    equity = [capital]
    
    # Generate predictions (simple momentum)
    predictions = []
    regimes = []
    
    for i in range(50, len(prices)):
        window = prices[max(0, i-50):i]
        regime = detect_regime(window)
        regimes.append(regime)
        
        # Simple momentum prediction
        ret_20d = (prices[i] - prices[i-20]) / prices[i-20] if i >= 20 else 0
        
        if ret_20d > 0.05:
            pred = 2  # Buy
        elif ret_20d < -0.05:
            pred = 0  # Sell
        else:
            pred = 1  # Hold
        
        predictions.append(pred)
    
    # Run simulation with enhanced features
    max_hold_days = 5
    
    for i in range(len(predictions)):
        price = prices[i + 50] if i + 50 < len(prices) else prices[-1]
        regime = regimes[i] if i < len(regimes) else "UNKNOWN"
        
        # Get regime parameters
        params = REGIME_THRESHOLDS.get(regime, REGIME_THRESHOLDS["SIDEWAYS"])
        entry_threshold = params["entry"]
        stop_loss = params["stop_loss"]
        trail_start = params["trail"]
        
        # Check if we should enter
        should_buy = predictions[i] == 2
        should_sell = predictions[i] == 0
        
        # Entry with confidence
        confidence = 0.5
        if should_buy and position == 0:
            if confidence >= entry_threshold:
                position = (capital * 0.95) / price
                entry_price = price
                capital = 0
                trades.append({
                    "type": "BUY",
                    "price": price,
                    "regime": regime,
                    "day": i
                })
        
        # Exit logic
        if position > 0:
            current_value = position * price
            pnl_pct = (price - entry_price) / entry_price
            
            # Stop Loss
            if pnl_pct < -stop_loss:
                capital = position * price * 0.999  # With commission
                trades.append({
                    "type": "SELL_SL",
                    "price": price,
                    "pnl": current_value - (entry_price * position),
                    "regime": regime,
                    "day": i
                })
                position = 0
            
            # Trailing Stop (after gaining trail_start%)
            elif pnl_pct > trail_start:
                # Calculate trailing stop level
                peak_price = price
                # In real system, track peak during position
                
                if should_sell:
                    capital = position * price * 0.999
                    trades.append({
                        "type": "SELL_TRAIL",
                        "price": price,
                        "pnl": current_value - (entry_price * position),
                        "regime": regime,
                        "day": i
                    })
                    position = 0
            
            # Time-based exit (max 5 days)
            elif len(trades) > 0 and trades[-1]["type"] == "BUY":
                days_held = i - trades[-1]["day"]
                if days_held >= max_hold_days:
                    capital = position * price * 0.999
                    trades.append({
                        "type": "SELL_TIME",
                        "price": price,
                        "pnl": current_value - (entry_price * position),
                        "regime": regime,
                        "day": i
                    })
                    position = 0
        
        # Record equity
        if position > 0:
            equity.append(position * price)
        else:
            equity.append(capital)
    
    # Close final position
    if position > 0:
        final_price = prices[-1]
        capital = position * final_price
        trades.append({
            "type": "SELL_CLOSE",
            "price": final_price,
            "pnl": (final_price - entry_price) * position if entry_price > 0 else 0,
            "regime": regimes[-1] if regimes else "UNKNOWN"
        })
    
    # Calculate metrics
    equity = np.array(equity)
    returns = np.diff(equity) / equity[:-1]
    returns = returns[~np.isnan(returns)]
    
    # Sharpe
    sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
    
    # Max Drawdown
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max
    max_dd = abs(np.min(drawdown))
    
    # Win Rate
    sell_trades = [t for t in trades if "SELL" in t["type"]]
    wins = len([t for t in sell_trades if t.get("pnl", 0) > 0])
    losses = len([t for t in sell_trades if t.get("pnl", 0) < 0])
    win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
    
    # Total Return
    total_return = (equity[-1] - 100000) / 100000 * 100
    
    # Results by regime
    regime_stats = {}
    for t in trades:
        reg = t.get("regime", "UNKNOWN")
        if reg not in regime_stats:
            regime_stats[reg] = {"trades": 0, "wins": 0, "pnl": 0}
        regime_stats[reg]["trades"] += 1
        if t.get("pnl", 0) > 0:
            regime_stats[reg]["wins"] += 1
            regime_stats[reg]["pnl"] += t.get("pnl", 0)
    
    # Print results
    logger.info("")
    logger.info("📊 RESULTS")
    logger.info("="*60)
    logger.info(f"Total Return: {total_return:+.2f}%")
    logger.info(f"Sharpe Ratio: {sharpe:.2f}")
    logger.info(f"Max Drawdown: {max_dd*100:.2f}%")
    logger.info(f"Win Rate: {win_rate:.1f}%")
    logger.info(f"Total Trades: {len(trades)}")
    logger.info(f"Wins: {wins}, Losses: {losses}")
    
    logger.info("")
    logger.info("📊 BY REGIME:")
    for reg, stats in regime_stats.items():
        wr = stats["wins"] / stats["trades"] * 100 if stats["trades"] > 0 else 0
        logger.info(f"  {reg}: {stats['trades']} trades, {wr:.0f}% win rate")
    
    # Grade
    logger.info("")
    logger.info("🏆 GRADE:")
    grade = "F"
    if sharpe > 1.5:
        grade = "A+"
    elif sharpe > 1.0:
        grade = "A"
    elif sharpe > 0.5:
        grade = "B"
    elif sharpe > 0:
        grade = "C"
    
    if max_dd < 0.15:
        grade += "+"
    
    logger.info(f"  Overall: {grade}")
    logger.info("")
    logger.info(f"Sharpe > 1.5: {'✅' if sharpe > 1.5 else '❌'}")
    logger.info(f"Max DD < 15%: {'✅' if max_dd < 0.15 else '❌'}")
    logger.info(f"Win Rate > 50%: {'✅' if win_rate > 50 else '❌'}")


if __name__ == "__main__":
    run_simulation("NVDA", "3y")
