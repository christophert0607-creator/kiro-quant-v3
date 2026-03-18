"""
Matrix Backtest: 10 Stocks × 10 Strategies = 100 Portfolios
Run all strategies on all stocks simultaneously in paper trading mode.
"""

import json
import time
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple
import asyncio

# Import data sources
import yfinance as yf


class Strategy:
    """Base strategy class"""
    name = "base"
    
    def __init__(self, params: dict = None):
        self.params = params or {}
    
    def generate_signal(self, df: pd.DataFrame) -> str:
        """Return: 'BUY', 'SELL', or 'HOLD'"""
        return 'HOLD'
    
    def get_params(self) -> dict:
        return self.params


class RSIStrategy(Strategy):
    name = "RSI"
    
    def generate_signal(self, df: pd.DataFrame) -> str:
        if 'RSI_14' not in df.columns or len(df) < 14:
            return 'HOLD'
        
        rsi = df['RSI_14'].iloc[-1]
        oversold = self.params.get('oversold', 30)
        overbought = self.params.get('overbought', 70)
        
        if rsi < oversold:
            return 'BUY'
        elif rsi > overbought:
            return 'SELL'
        return 'HOLD'


class MACDStrategy(Strategy):
    name = "MACD"
    
    def generate_signal(self, df: pd.DataFrame) -> str:
        if 'MACD' not in df.columns or 'MACD_SIGNAL' not in df.columns or len(df) < 2:
            return 'HOLD'
        
        macd = df['MACD'].iloc[-1]
        signal = df['MACD_SIGNAL'].iloc[-1]
        prev_macd = df['MACD'].iloc[-2]
        prev_signal = df['MACD_SIGNAL'].iloc[-2]
        
        # Golden cross
        if prev_macd <= prev_signal and macd > signal:
            return 'BUY'
        # Death cross
        elif prev_macd >= prev_signal and macd < signal:
            return 'SELL'
        return 'HOLD'


class MACrossStrategy(Strategy):
    name = "MA_Cross"
    
    def generate_signal(self, df: pd.DataFrame) -> str:
        if 'MA_5' not in df.columns or 'MA_20' not in df.columns or len(df) < 20:
            return 'HOLD'
        
        ma5 = df['MA_5'].iloc[-1]
        ma20 = df['MA_20'].iloc[-1]
        prev_ma5 = df['MA_5'].iloc[-2]
        prev_ma20 = df['MA_20'].iloc[-2]
        
        if prev_ma5 <= prev_ma20 and ma5 > ma20:
            return 'BUY'
        elif prev_ma5 >= prev_ma20 and ma5 < ma20:
            return 'SELL'
        return 'HOLD'


class BBandsStrategy(Strategy):
    name = "Bollinger"
    
    def generate_signal(self, df: pd.DataFrame) -> str:
        if 'BB_UPPER' not in df.columns or 'BB_LOWER' not in df.columns:
            return 'HOLD'
        
        price = df['Close'].iloc[-1]
        upper = df['BB_UPPER'].iloc[-1]
        lower = df['BB_LOWER'].iloc[-1]
        
        if price <= lower:
            return 'BUY'
        elif price >= upper:
            return 'SELL'
        return 'HOLD'


class SupportResistanceStrategy(Strategy):
    name = "SR"
    
    def generate_signal(self, df: pd.DataFrame) -> str:
        if 'Low' not in df.columns or 'High' not in df.columns:
            return 'HOLD'
        
        price = df['Close'].iloc[-1]
        window = self.params.get('window', 10)
        support = df['Low'].tail(window).min()
        resistance = df['High'].tail(window).max()
        
        tolerance = self.params.get('tolerance', 0.02)
        
        if price <= support * (1 + tolerance):
            return 'BUY'
        elif price >= resistance * (1 - tolerance):
            return 'SELL'
        return 'HOLD'


class MomentumStrategy(Strategy):
    name = "Momentum"
    
    def generate_signal(self, df: pd.DataFrame) -> str:
        if 'RSI_14' not in df.columns or 'MA_20' not in df.columns:
            return 'HOLD'
        
        rsi = df['RSI_14'].iloc[-1]
        price = df['Close'].iloc[-1]
        ma20 = df['MA_20'].iloc[-1]
        
        if rsi > 50 and price > ma20:
            return 'BUY'
        elif rsi < 50 or price < ma20:
            return 'SELL'
        return 'HOLD'


class MeanReversionStrategy(Strategy):
    name = "MeanRev"
    
    def generate_signal(self, df: pd.DataFrame) -> str:
        if 'MA_20' not in df.columns:
            return 'HOLD'
        
        price = df['Close'].iloc[-1]
        ma20 = df['MA_20'].iloc[-1]
        deviation = (price - ma20) / ma20
        
        threshold = self.params.get('threshold', 0.05)
        
        if deviation < -threshold:
            return 'BUY'
        elif deviation > threshold:
            return 'SELL'
        return 'HOLD'


class VolumeSpikeStrategy(Strategy):
    name = "Volume"
    
    def generate_signal(self, df: pd.DataFrame) -> str:
        if 'Volume' not in df.columns or 'Close' not in df.columns or len(df) < 20:
            return 'HOLD'
        
        price_change = df['Close'].pct_change().iloc[-1]
        avg_volume = df['Volume'].tail(20).mean()
        current_volume = df['Volume'].iloc[-1]
        
        volume_ratio = current_volume / avg_volume
        
        if volume_ratio > 1.5 and price_change > 0.02:
            return 'BUY'
        elif price_change < -0.03:
            return 'SELL'
        return 'HOLD'


class KellyRSIStrategy(Strategy):
    name = "KellyRSI"
    
    def generate_signal(self, df: pd.DataFrame) -> str:
        if 'RSI_14' not in df.columns:
            return 'HOLD'
        
        rsi = df['RSI_14'].iloc[-1]
        
        # Kelly criterion calculation
        win_rate = self.params.get('win_rate', 0.55)
        avg_win = self.params.get('avg_win', 1.03)
        avg_loss = self.params.get('avg_loss', 0.98)
        
        kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
        kelly = max(0, min(kelly, 0.25))  # Cap at 25%
        
        if rsi < 30 and kelly > 0.1:
            return 'BUY'
        elif rsi > 70:
            return 'SELL'
        return 'HOLD'


class MLStrategy(Strategy):
    name = "ML"
    
    def generate_signal(self, df: pd.DataFrame) -> str:
        # Use simple moving average prediction as proxy
        if len(df) < 10:
            return 'HOLD'
        
        # Simple prediction: if price > MA5, expect uptrend
        if 'MA_5' not in df.columns:
            return 'HOLD'
        
        price = df['Close'].iloc[-1]
        ma5 = df['MA_5'].iloc[-1]
        
        if price > ma5 * 1.01:
            return 'BUY'
        elif price < ma5 * 0.99:
            return 'SELL'
        return 'HOLD'


# Strategy registry
STRATEGIES = {
    'RSI': RSIStrategy,
    'MACD': MACDStrategy,
    'MA_Cross': MACrossStrategy,
    'Bollinger': BBandsStrategy,
    'SR': SupportResistanceStrategy,
    'Momentum': MomentumStrategy,
    'MeanRev': MeanReversionStrategy,
    'Volume': VolumeSpikeStrategy,
    'KellyRSI': KellyRSIStrategy,
    'ML': MLStrategy,
}


class MatrixPortfolio:
    """Single portfolio for one stock + one strategy"""
    
    def __init__(self, symbol: str, strategy: Strategy, initial_capital: float = 10000):
        self.symbol = symbol
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.position = 0
        self.trades = []
        self.history = []
    
    def run_cycle(self, df: pd.DataFrame, current_price: float):
        """Execute one trading cycle"""
        signal = self.strategy.generate_signal(df)
        
        entry = {
            'time': datetime.now(),
            'price': current_price,
            'signal': signal,
            'cash': self.cash,
            'position': self.position
        }
        
        if signal == 'BUY' and self.position == 0 and self.cash > 0:
            # Buy
            qty = int(self.cash / current_price)
            if qty > 0:
                cost = qty * current_price
                self.cash -= cost
                self.position = qty
                entry['action'] = 'BUY'
                entry['qty'] = qty
                entry['cost'] = cost
                self.trades.append(entry)
        
        elif signal == 'SELL' and self.position > 0:
            # Sell
            proceeds = self.position * current_price
            self.cash += proceeds
            entry['action'] = 'SELL'
            entry['qty'] = self.position
            entry['proceeds'] = proceeds
            self.trades.append(entry)
            self.position = 0
        
        else:
            entry['action'] = 'HOLD'
        
        self.history.append(entry)
    
    def get_pnl(self, current_price: float) -> float:
        """Calculate current P&L"""
        position_value = self.position * current_price
        return (self.cash + position_value - self.initial_capital) / self.initial_capital * 100


class MatrixBacktester:
    """Run 10x10 matrix backtest"""
    
    def __init__(self, stocks: List[str], initial_capital: float = 10000):
        self.stocks = stocks
        self.initial_capital = initial_capital
        self.portfolios: Dict[Tuple[str, str], MatrixPortfolio] = {}
        
        # Create all portfolios
        for stock in stocks:
            for strategy_name, strategy_class in STRATEGIES.items():
                strategy = strategy_class()
                portfolio = MatrixPortfolio(stock, strategy, initial_capital)
                self.portfolios[(stock, strategy_name)] = portfolio
    
    def fetch_data(self, symbol: str, period: str = "3mo") -> pd.DataFrame:
        """Fetch stock data"""
        try:
            # Try yfinance first
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period)
            
            if df.empty:
                return pd.DataFrame()
            
            # Calculate indicators
            df['RSI_14'] = self._calculate_rsi(df['Close'])
            df['MA_5'] = df['Close'].rolling(5).mean()
            df['MA_20'] = df['Close'].rolling(20).mean()
            
            # MACD
            exp12 = df['Close'].ewm(span=12, adjust=False).mean()
            exp26 = df['Close'].ewm(span=26, adjust=False).mean()
            df['MACD'] = exp12 - exp26
            df['MACD_SIGNAL'] = df['MACD'].ewm(span=9, adjust=False).mean()
            
            # Bollinger Bands
            bb = df['Close'].rolling(20).std()
            df['BB_UPPER'] = df['MA_20'] + (bb * 2)
            df['BB_LOWER'] = df['MA_20'] - (bb * 2)
            
            # Volume
            df['Volume'] = df['Volume'] if 'Volume' in df.columns else 0
            
            return df
            
        except Exception as e:
            print(f"Error fetching {symbol}: {e}")
            return pd.DataFrame()
    
    def _calculate_rsi(self, prices, period: int = 14):
        """Calculate RSI"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def run(self, cycles: int = 30):
        """Run the matrix backtest"""
        print(f"Running 10x10 Matrix Backtest...")
        print(f"Stocks: {len(self.stocks)}")
        print(f"Strategies: {len(STRATEGIES)}")
        print(f"Total Portfolios: {len(self.portfolios)}")
        print("=" * 50)
        
        results = {}
        
        # Fetch data for all stocks once
        stock_data = {}
        for stock in self.stocks:
            print(f"Fetching data for {stock}...")
            stock_data[stock] = self.fetch_data(stock)
        
        # Run cycles
        for cycle in range(cycles):
            for stock in self.stocks:
                df = stock_data[stock]
                if df.empty or len(df) < 30:
                    continue
                
                # Get current price (simulate cycling through data)
                idx = min(30 + cycle, len(df) - 1)
                current_price = float(df['Close'].iloc[idx])
                window_df = df.iloc[:idx+1]
                
                for strategy_name in STRATEGIES.keys():
                    portfolio = self.portfolios[(stock, strategy_name)]
                    portfolio.run_cycle(window_df, current_price)
        
        # Calculate results
        print("\n" + "=" * 50)
        print("RESULTS:")
        print("=" * 50)
        
        # Build results matrix
        for stock in self.stocks:
            df = stock_data.get(stock)
            if df is None or df.empty:
                continue
            
            current_price = float(df['Close'].iloc[-1])
            
            for strategy_name in STRATEGIES.keys():
                portfolio = self.portfolios[(stock, strategy_name)]
                pnl = portfolio.get_pnl(current_price)
                results[(stock, strategy_name)] = {
                    'pnl': pnl,
                    'trades': len(portfolio.trades),
                    'final_value': portfolio.cash + portfolio.position * current_price
                }
        
        return results
    
    def print_matrix(self, results: Dict):
        """Print results in matrix format"""
        # Header
        print("\n" + "=" * 80)
        print("STRATEGY MATRIX RESULTS (P&L %)")
        print("=" * 80)
        
        # Column headers
        strategies = list(STRATEGIES.keys())
        
        # Print header
        header = f"{'Stock':<10}"
        for s in strategies:
            header += f" {s:>8}"
        print(header)
        print("-" * 80)
        
        # Print each row
        for stock in self.stocks:
            row = f"{stock:<10}"
            for strategy in strategies:
                key = (stock, strategy)
                if key in results:
                    pnl = results[key]['pnl']
                    color = "+" if pnl > 0 else ""
                    row += f" {color}{pnl:>7.1f}%"
                else:
                    row += f" {'N/A':>8}"
            print(row)
        
        # Summary
        print("-" * 80)
        print("\nTOP 10 PERFORMERS:")
        
        sorted_results = sorted(results.items(), key=lambda x: x[1]['pnl'], reverse=True)
        for i, (key, val) in enumerate(sorted_results[:10]):
            print(f"  {i+1}. {key[0]} + {key[1]}: {val['pnl']:+.2f}% ({val['trades']} trades)")
        
        print("\nBOTTOM 10:")
        for i, (key, val) in enumerate(sorted_results[-10:]):
            print(f"  {i+1}. {key[0]} + {key[1]}: {val['pnl']:+.2f}% ({val['trades']} trades)")


def run_matrix_backtest():
    """Main entry point"""
    # 10 stocks
    stocks = [
        "AAPL", "TSLA", "NVDA", "AMD", "META",
        "GOOGL", "AMZN", "MSFT", "COIN", "00700.HK"
    ]
    
    # Run backtest
    tester = MatrixBacktester(stocks, initial_capital=10000)
    results = tester.run(cycles=30)
    
    # Print results
    tester.print_matrix(results)
    
    # Save results
    with open('matrix_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print("\n✓ Results saved to matrix_results.json")


if __name__ == "__main__":
    run_matrix_backtest()
