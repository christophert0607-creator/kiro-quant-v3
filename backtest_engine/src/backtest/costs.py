"""
Transaction Costs & Slippage Model
"""

import polars as pl
import numpy as np
from typing import Dict, Optional


class TransactionCosts:
    """交易成本模型"""
    
    # 市場配置
    MARKET_CONFIG = {
        'HK': {
            'commission': 0.0008,  # 0.08%
            'min_fee': 100,        # HKD 100
            'stamp_duty': 0.001,  # 0.1% (sell only)
            'currency': 'HKD'
        },
        'US': {
            'commission': 0.005,   # $0.005/share
            'min_fee': 1.0,        # $1.00
            'stamp_duty': 0,
            'currency': 'USD'
        },
        'A股': {
            'commission': 0.0003, # 0.03%
            'min_fee': 5,          # CNY 5
            'stamp_duty': 0.001,   # 0.1% (sell only)
            'currency': 'CNY'
        },
        'SG': {
            'commission': 0.002,   # 0.2%
            'min_fee': 25,         # SGD 25
            'stamp_duty': 0,
            'currency': 'SGD'
        },
        'JP': {
            'commission': 0.0005, # 0.05%
            'min_fee': 50,         # JPY 50
            'stamp_duty': 0,
            'currency': 'JPY'
        },
        'AU': {
            'commission': 0.001,  # 0.1%
            'min_fee': 10,         # AUD 10
            'stamp_duty': 0,
            'currency': 'AUD'
        }
    }
    
    def __init__(self, market: str = 'US'):
        self.market = market
        self.config = self.MARKET_CONFIG.get(market, self.MARKET_CONFIG['US'])
        
    def calculate(self, price: float, quantity: float, side: str = 'buy') -> float:
        """
        計算交易成本
        
        Args:
            price: 股價
            quantity: 股數
            side: 'buy' or 'sell'
            
        Returns:
            總成本 ( commission + stamp_duty + min_fee )
        """
        turnover = price * quantity
        
        # Commission
        commission = turnover * self.config['commission']
        commission = max(commission, self.config['min_fee'])
        
        # Stamp duty (only on sell for HK/A股)
        stamp_duty = 0
        if side == 'sell' and self.config['stamp_duty'] > 0:
            stamp_duty = turnover * self.config['stamp_duty']
            
        total_cost = commission + stamp_duty
        
        return total_cost
    
    def calculate_pct(self, price: float, quantity: float, side: str = 'buy') -> float:
        """計算交易成本百分比"""
        turnover = price * quantity
        cost = self.calculate(price, quantity, side)
        return cost / turnover if turnover > 0 else 0


class SlippageModel:
    """滑點模型"""
    
    def __init__(self, base_slippage: float = 0.0005):
        """
        Args:
            base_slippage: 基礎滑點 (default 0.05%)
        """
        self.base_slippage = base_slippage
        
    def calculate(self, price: float, volume: float, volatility: float = 0.02,
                 side: str = 'buy') -> float:
        """
        計算滑點
        
        Args:
            price: 股價
            volume: 成交量
            volatility: 波幅 (default 2%)
            side: 'buy' or 'sell'
            
        Returns:
            滑點金額
        """
        # 動態滑點 = 基礎 + 波幅調整 + 成交量調整
        slippage = self.base_slippage
        
        # 波幅調整
        slippage += volatility * 0.1
        
        # 成交量調整 (成交量越大，滑點越大)
        volume_factor = min(volume / 1000000, 1.0)  # Cap at 1
        slippage += volume_factor * 0.001
        
        slippage_amount = price * slippage
        
        # Buy: 向上滑; Sell: 向下滑
        if side == 'sell':
            slippage_amount = -slippage_amount
            
        return slippage_amount
    
    def calculate_pct(self, volume: float, volatility: float = 0.02) -> float:
        """計算滑點百分比"""
        slippage = self.base_slippage
        slippage += volatility * 0.1
        volume_factor = min(volume / 1000000, 1.0)
        slippage += volume_factor * 0.001
        return slippage


class KellyPositionSizer:
    """Kelly Criterion 倉位計算"""
    
    def __init__(self, fraction: float = 0.25):
        """
        Args:
            fraction: Kelly fraction (default 0.25 = 1/4 Kelly)
        """
        self.fraction = fraction
        
    def calculate(self, win_rate: float, avg_win: float, avg_loss: float,
                  max_position: float = 0.2) -> float:
        """
        計算倉位大小
        
        Args:
            win_rate: 勝率 (0-1)
            avg_win: 平均盈利
            avg_loss: 平均虧損
            max_position: 最大倉位限制
            
        Returns:
            倉位百分比 (0-1)
        """
        if avg_loss == 0 or win_rate <= 0 or win_rate >= 1:
            return 0
            
        # Win/Loss ratio
        wl_ratio = avg_win / avg_loss
        
        # Kelly %
        kelly = win_rate - (1 - win_rate) / wl_ratio
        
        # Fractional Kelly
        position = kelly * self.fraction
        
        # 限制最大倉位
        position = min(position, max_position)
        
        # 至少要有正既期望值先做
        return max(0, position)
    
    def calculate_from_trades(self, trades: list, max_position: float = 0.2) -> float:
        """
        從交易歷史計算倉位
        
        Args:
            trades: 交易列表 [profit1, profit2, ...]
            max_position: 最大倉位限制
        """
        if not trades:
            return 0
            
        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t < 0]
        
        if not wins or not losses:
            return 0
            
        win_rate = len(wins) / len(trades)
        avg_win = sum(wins) / len(wins)
        avg_loss = abs(sum(losses) / len(losses))
        
        return self.calculate(win_rate, avg_win, avg_loss, max_position)


class RiskManager:
    """風險管理器"""
    
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {
            'stop_loss': 0.05,      # 5%
            'take_profit': 0.15,    # 15%
            'max_position': 0.20,    # 20%
            'max_dd_stop': 0.15,    # 15%
            'max_dd_pause': 0.20,    # 20%
            'daily_loss_limit': 0.05  # 5%
        }
        
    def should_stop_loss(self, current_price: float, entry_price: float) -> bool:
        """是否應該止損"""
        pnl_pct = (current_price - entry_price) / entry_price
        return pnl_pct <= -self.config['stop_loss']
    
    def should_take_profit(self, current_price: float, entry_price: float) -> bool:
        """是否應該止盈"""
        pnl_pct = (current_price - entry_price) / entry_price
        return pnl_pct >= self.config['take_profit']
    
    def should_pause(self, current_dd: float) -> bool:
        """是否應該暫停交易"""
        return current_dd >= self.config['max_dd_pause']
    
    def should_stop(self, current_dd: float) -> bool:
        """是否應該停止交易"""
        return current_dd >= self.config['max_dd_stop']
    
    def check_daily_limit(self, daily_pnl: float, capital: float) -> bool:
        """檢查每日虧損限制"""
        return daily_pnl / capital <= -self.config['daily_loss_limit']
