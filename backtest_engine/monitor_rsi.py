#!/usr/bin/env python3
"""
RSI Paper Trading Monitor
Strategy: RSI(9,26,71)
Stocks: HK & US
"""

import sys
sys.path.insert(0, '.')
from src.backtest.engine import BacktestEngine
import yfinance as yf

def calculate_rsi(prices, period=9):
    """Calculate RSI"""
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_signal(rsi_value):
    """Get trading signal"""
    if rsi_value < 26:
        return 'BUY', 'Oversold'
    elif rsi_value > 71:
        return 'SELL', 'Overbought'
    else:
        return 'HOLD', 'Neutral'

def main():
    stocks = [
        ('0700.HK', 'Tencent', 'HK'),
        ('9988.HK', 'Alibaba', 'HK'),
        ('3690.HK', 'Meituan', 'HK'),
        ('0005.HK', 'HSBC', 'HK'),
        ('AAPL', 'Apple', 'US'),
        ('NVDA', 'Nvidia', 'US'),
        ('MSFT', 'Microsoft', 'US'),
    ]
    
    params = {'period': 9, 'oversold': 26, 'overbought': 71}
    
    print('=' * 60)
    print('RSI Paper Trading Monitor')
    print('Strategy: RSI(9,26,71)')
    print('=' * 60)
    print()
    
    buy_signals = []
    
    for sym, name, market in stocks:
        try:
            df = yf.download(sym, period='60d', progress=False)
            if len(df) > 10:
                close = df['Close']
                if hasattr(close, 'values'):
                    close = close.values.flatten()
                
                rsi = calculate_rsi(pd.Series(close), 9)
                latest_rsi = rsi.iloc[-1]
                
                signal, reason = get_signal(latest_rsi)
                
                # Get latest price
                latest_price = close[-1]
                
                print(f'{name:15} ({sym:8}) RSI={latest_rsi:5.1f} → {signal:4} ({reason})')
                
                if signal == 'BUY':
                    buy_signals.append((name, sym, latest_rsi, latest_price))
                    
        except Exception as e:
            print(f'{name}: Error - {e}')
    
    print()
    if buy_signals:
        print('=' * 60)
        print('BUY SIGNALS:')
        print('=' * 60)
        for name, sym, rsi, price in buy_signals:
            print(f'  {name} ({sym}) @ ${price:.2f} (RSI={rsi:.1f})')
    else:
        print('No BUY signals currently.')
    
    return buy_signals

if __name__ == '__main__':
    import pandas as pd
    main()
