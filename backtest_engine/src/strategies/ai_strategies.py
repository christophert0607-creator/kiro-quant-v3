"""
AI Discovered Strategies - Pattern-based Trading

Based on AI Pattern Discovery Experiment
"""

import pandas as pd
import numpy as np
from typing import Dict, List


class ConsolidationBreakoutStrategy:
    """Strategy 1: Trend Continuation After Consolidation"""
    
    def __init__(self, 
                 consolidation_threshold: float = 0.026,  # 2.6% daily range
                 breakout_multiplier: float = 1.2,  # volume > 1.2x avg
                 profit_target: float = 0.08,  # 8%
                 stop_loss: float = 0.05):   # 5%
        self.consolidation_threshold = consolidation_threshold
        self.breakout_multiplier = breakout_multiplier
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Generate trading signals"""
        signals = pd.Series(0, index=df.index)
        
        # Calculate daily range
        df['daily_range'] = (df['High'] - df['Low']) / df['Close']
        
        # 20-day average volume
        df['avg_volume'] = df['Volume'].rolling(20).mean()
        
        # Identify consolidation (low volatility)
        df['is_consolidating'] = df['daily_range'] < df['daily_range'].quantile(0.25)
        
        # 5-day high (shift first, then rolling)
        df['high_5'] = df['High'].shift(1).rolling(5).max()
        
        # Breakout: price breaks 5-day high with volume
        df['breakout'] = (df['Close'] > df['high_5']) & (df['Volume'] > df['avg_volume'] * self.breakout_multiplier)
        
        # Entry signals
        df['signal'] = 0
        df.loc[(df['is_consolidating'].shift(1)) & df['breakout'], 'signal'] = 1  # Buy
        
        return df['signal']
    
    def backtest(self, df: pd.DataFrame) -> Dict:
        """Run backtest"""
        signals = self.generate_signals(df)
        
        equity = [100000]
        position = 0
        entry_price = 0
        trades = []
        
        for i in range(1, len(df)):
            equity.append(equity[-1])
            
            if signals.iloc[i] == 1 and position == 0:
                # Buy
                entry_price = df['Close'].iloc[i]
                position = equity[-1] * 0.1 / entry_price  # 10% position
                
            elif position > 0:
                pnl_pct = (df['Close'].iloc[i] - entry_price) / entry_price
                
                # Exit conditions
                if pnl_pct >= self.profit_target or pnl_pct <= -self.stop_loss:
                    equity[-1] += (df['Close'].iloc[i] - entry_price) * position
                    trades.append((df['Close'].iloc[i] - entry_price) * position)
                    position = 0
                    entry_price = 0
        
        # Calculate metrics
        returns = np.diff(equity) / equity[:-1]
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
        
        return {
            'total_return': (equity[-1] - 100000) / 100000,
            'sharpe_ratio': sharpe,
            'win_rate': len([t for t in trades if t > 0]) / len(trades) if trades else 0,
            'n_trades': len(trades),
            'trades': trades,
            'equity': equity
        }


class MeanReversionStrategy:
    """Strategy 2: Mean Reversion with Trend Confirmation"""
    
    def __init__(self,
                 ma_short: int = 20,
                 ma_long: int = 50,
                 volume_multiplier: float = 1.3,
                 profit_target: float = 0.08,
                 stop_loss: float = 0.05):
        self.ma_short = ma_short
        self.ma_long = ma_long
        self.volume_multiplier = volume_multiplier
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Generate trading signals"""
        signals = pd.Series(0, index=df.index)
        
        # Moving averages
        df['ma_short'] = df['Close'].rolling(self.ma_short).mean()
        df['ma_long'] = df['Close'].rolling(self.ma_long).mean()
        
        # Trend: price above long MA
        df['in_uptrend'] = df['Close'] > df['ma_long']
        
        # Pullback: price below short MA but above long MA
        df['pullback'] = (df['Close'] < df['ma_short']) & (df['Close'] > df['ma_long'])
        
        # Volume confirmation
        df['avg_volume'] = df['Volume'].rolling(20).mean()
        df['volume_confirm'] = df['Volume'] > df['avg_volume'] * self.volume_multiplier
        
        # Green candle
        df['green_candle'] = df['Close'] > df['Open']
        
        # Entry signal
        df['signal'] = 0
        df.loc[df['pullback'] & df['in_uptrend'] & df['volume_confirm'] & df['green_candle'], 'signal'] = 1
        
        return df['signal']
    
    def backtest(self, df: pd.DataFrame) -> Dict:
        """Run backtest"""
        signals = self.generate_signals(df)
        
        equity = [100000]
        position = 0
        entry_price = 0
        trades = []
        
        for i in range(1, len(df)):
            equity.append(equity[-1])
            
            if signals.iloc[i] == 1 and position == 0:
                entry_price = df['Close'].iloc[i]
                position = equity[-1] * 0.1 / entry_price
                
            elif position > 0:
                pnl_pct = (df['Close'].iloc[i] - entry_price) / entry_price
                
                if pnl_pct >= self.profit_target or pnl_pct <= -self.stop_loss:
                    equity[-1] += (df['Close'].iloc[i] - entry_price) * position
                    trades.append((df['Close'].iloc[i] - entry_price) * position)
                    position = 0
        
        returns = np.diff(equity) / equity[:-1]
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
        
        return {
            'total_return': (equity[-1] - 100000) / 100000,
            'sharpe_ratio': sharpe,
            'win_rate': len([t for t in trades if t > 0]) / len(trades) if trades else 0,
            'n_trades': len(trades),
            'trades': trades,
            'equity': equity
        }


# Test
if __name__ == "__main__":
    import yfinance as yf
    
    # Fetch NVDA data
    ticker = yf.Ticker('NVDA')
    df = ticker.history(start='2020-01-01', end='2025-03-17')
    df = df.reset_index()
    df = df.rename(columns={'Date': 'date', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'})
    
    print("=== AI Discovered Strategies Test ===\n")
    
    # Strategy 1: Consolidation Breakout
    s1 = ConsolidationBreakoutStrategy()
    result1 = s1.backtest(df)
    print("Strategy 1: Consolidation Breakout")
    print(f"  Return: {result1['total_return']:.2%}")
    print(f"  Sharpe: {result1['sharpe_ratio']:.2f}")
    print(f"  Win Rate: {result1['win_rate']:.2%}")
    print(f"  Trades: {result1['n_trades']}\n")
    
    # Strategy 2: Mean Reversion
    s2 = MeanReversionStrategy()
    result2 = s2.backtest(df)
    print("Strategy 2: Mean Reversion with Trend")
    print(f"  Return: {result2['total_return']:.2%}")
    print(f"  Sharpe: {result2['sharpe_ratio']:.2f}")
    print(f"  Win Rate: {result2['win_rate']:.2%}")
    print(f"  Trades: {result2['n_trades']}")
