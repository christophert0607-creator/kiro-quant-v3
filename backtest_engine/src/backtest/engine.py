"""
Backtest Engine - Core

Polars 加速版本
"""

import polars as pl
import numpy as np
from typing import Dict, List, Optional, Tuple
import yaml
from pathlib import Path


class BacktestEngine:
    """回測引擎 - Polars 版本"""
    
    def __init__(self, config_path: str = "./config"):
        self.config = self._load_config(config_path)
        self.results = []
        
    def _load_config(self, config_path: str) -> Dict:
        """載入配置"""
        config_dir = Path(config_path)
        
        with open(config_dir / "backtest.yaml") as f:
            backtest_cfg = yaml.safe_load(f)
            
        with open(config_dir / "risk.yaml") as f:
            risk_cfg = yaml.safe_load(f)
            
        return {**backtest_cfg, **risk_cfg}
    
    def run(self, df, indicator_name: str, 
            params: Dict, market: str = "US") -> Dict:
        """
        運行單次回測
        
        Args:
            df: Polars DataFrame 或 Pandas DataFrame
            indicator_name: 指標名稱 (RSI, MACD, etc.)
            params: 指標參數
            market: 市場 (用於計算交易費用)
            
        Returns:
            回測結果
        """
        from ..indicators.indicators import get_indicator
        
        # 確保係 Polars
        if hasattr(df, 'to_pandas'):
            df = df.to_pandas()
        df = pl.from_pandas(df)
        
        # 計算指標
        indicator = get_indicator(indicator_name)
        
        # 轉換為 pandas 計算指標，然後轉回 polars
        df_pd = df.to_pandas()
        df_pd = indicator.calculate(df_pd.copy(), **params)
        df = pl.from_pandas(df_pd)
        
        # 生成信號
        signal_pd = indicator.generate_signal(df_pd, **params)
        
        # 運行回測
        result = self._run_vectorized(
            df['close'].to_numpy(),
            signal_pd.values,
            self.config['risk']['exit']['stop_loss'],
            self.config['risk']['exit']['take_profit'],
            self.config['backtest']['slippage']['phase1_fixed'],
            self.config['backtest']['initial_capital']
        )
        
        # 計算風險指標
        result['sharpe'] = self._calculate_sharpe(result['returns'])
        result['max_drawdown'] = self._calculate_max_dd(result['equity'])
        result['win_rate'] = self._calculate_win_rate(result['trades'])
        
        return result
    
    @staticmethod
    def _run_vectorized(prices: np.ndarray, signals: np.ndarray,
                       stop_loss: float, take_profit: float,
                       slippage: float, initial_capital: float) -> Dict:
        """
        Polars-style 高效回測
        """
        n = len(prices)
        equity = np.zeros(n)
        equity[0] = initial_capital
        position = 0
        entry_price = 0
        trades = []
        
        for i in range(1, n):
            equity[i] = equity[i-1]
            
            if np.isnan(prices[i]) or signals[i] == 0:
                continue
                
            # Buy signal
            if signals[i] == 1 and position == 0:
                buy_price = prices[i] * (1 + slippage)
                shares = equity[i] * 0.1 / buy_price
                position = shares
                entry_price = buy_price
                
            # Sell signal
            elif signals[i] == -1 and position > 0:
                sell_price = prices[i] * (1 - slippage)
                pnl = (sell_price - entry_price) * position
                equity[i] += pnl
                trades.append(pnl)
                position = 0
                entry_price = 0
                
            # Stop loss / Take profit
            if position > 0:
                current_price = prices[i]
                pnl_pct = (current_price - entry_price) / entry_price
                
                if pnl_pct <= -stop_loss:
                    sell_price = current_price * (1 - slippage)
                    pnl = (sell_price - entry_price) * position
                    equity[i] += pnl
                    trades.append(pnl)
                    position = 0
                    entry_price = 0
                    
                elif pnl_pct >= take_profit:
                    sell_price = current_price * (1 - slippage)
                    pnl = (sell_price - entry_price) * position
                    equity[i] += pnl
                    trades.append(pnl)
                    position = 0
                    entry_price = 0
        
        # Close remaining position
        if position > 0:
            sell_price = prices[-1] * (1 - slippage)
            pnl = (sell_price - entry_price) * position
            equity[-1] += pnl
            trades.append(pnl)
            
        # Calculate returns
        returns = np.diff(equity) / equity[:-1]
        returns = np.insert(returns, 0, 0)
        
        return {
            'equity': equity.tolist(),
            'returns': returns.tolist(),
            'trades': [float(t) for t in trades],
            'final_value': float(equity[-1]),
            'total_return': float((equity[-1] - initial_capital) / initial_capital),
            'n_trades': len(trades)
        }
    
    @staticmethod
    def _calculate_sharpe(returns: List[float]) -> float:
        """計算 Sharpe Ratio"""
        if not returns or len(returns) < 2:
            return 0
            
        returns = np.array(returns)
        returns = returns[~np.isnan(returns)]
        
        if len(returns) == 0 or np.std(returns) == 0:
            return 0
            
        return np.mean(returns) / np.std(returns) * np.sqrt(252)
    
    @staticmethod
    def _calculate_max_dd(equity: List[float]) -> float:
        """計算最大回撤"""
        equity = np.array(equity)
        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / peak
        return float(np.max(drawdown))
    
    @staticmethod
    def _calculate_win_rate(trades: List[float]) -> float:
        """計算勝率"""
        if not trades:
            return 0
        wins = sum(1 for t in trades if t > 0)
        return wins / len(trades)


def generate_param_combinations(indicator_name: str) -> List[Dict]:
    """生成參數組合"""
    import yaml
    
    with open("./config/indicators.yaml") as f:
        config = yaml.safe_load(f)
    
    indicator_cfg = config['indicators'][indicator_name]
    params = indicator_cfg['params']
    
    import itertools
    
    param_names = list(params.keys())
    param_values = list(params.values())
    
    combinations = []
    for combo in itertools.product(*param_values):
        combo_dict = dict(zip(param_names, combo))
        
        if indicator_name == 'MACD':
            if combo_dict['fast'] >= combo_dict['slow']:
                continue
                
        combinations.append(combo_dict)
    
    return combinations
