"""
Optuna Bayesian Optimization for 8×8×139 Backtest

比 Grid Search 快 10-100x！
"""

import optuna
from optuna.samplers import TPESampler
import pandas as pd
from typing import Dict, List, Optional
import yaml


class BayesianOptimizer:
    """Bayesian Optimization using Optuna"""
    
    def __init__(self, n_trials: int = 100, direction: str = 'maximize'):
        """
        Args:
            n_trials: Number of optimization trials
            direction: 'maximize' or 'minimize'
        """
        self.n_trials = n_trials
        self.direction = direction
        self.study = None
        
    def create_study(self, study_name: str = "8x8x139"):
        """創建 Optuna study"""
        sampler = TPESampler(seed=42)
        self.study = optuna.create_study(
            direction=self.direction,
            sampler=sampler,
            study_name=study_name
        )
        return self.study
    
    def optimize(self, objective_func, n_trials: Optional[int] = None):
        """運行優化"""
        n = n_trials or self.n_trials
        self.study.optimize(objective_func, n_trials=n, show_progress_bar=True)
        return self.study
    
    def get_best_params(self) -> Dict:
        """獲取最佳參數"""
        return self.study.best_params
    
    def get_top_n(self, n: int = 10) -> List[Dict]:
        """獲取 Top N 參數"""
        trials = self.study.trials
        # Sort by value
        sorted_trials = sorted(trials, key=lambda x: x.value if x.value else 0, reverse=True)
        return [t.params for t in sorted_trials[:n]]


class IndicatorObjective:
    """指標優化目標函數"""
    
    def __init__(self, df: pd.DataFrame, indicator: str, 
                 metric: str = 'sharpe', backtest_engine=None):
        self.df = df
        self.indicator = indicator
        self.metric = metric
        self.engine = backtest_engine
        
    def __call__(self, trial: optuna.Trial):
        """Objective function for Optuna"""
        
        # Define parameter search space based on indicator
        if self.indicator == 'RSI':
            params = {
                'period': trial.suggest_int('period', 5, 30),
                'oversold': trial.suggest_int('oversold', 20, 40),
                'overbought': trial.suggest_int('overbought', 60, 80)
            }
            
        elif self.indicator == 'MACD':
            params = {
                'fast': trial.suggest_int('fast', 5, 20),
                'slow': trial.suggest_int('slow', 20, 50),
                'signal': trial.suggest_int('signal', 5, 15)
            }
            
        elif self.indicator == 'BB':
            params = {
                'period': trial.suggest_int('period', 10, 30),
                'std': trial.suggest_float('std', 1.5, 3.0)
            }
            
        elif self.indicator == 'KDJ':
            params = {
                'period': trial.suggest_int('period', 5, 25),
                'k': 3,
                'd': 3
            }
            
        elif self.indicator == 'CCI':
            params = {
                'period': trial.suggest_int('period', 10, 30)
            }
            
        else:
            params = {'period': 14}
        
        # Run backtest
        try:
            if self.engine:
                result = self.engine.run(self.df, self.indicator, params, 'US')
                
                if self.metric == 'sharpe':
                    return result.get('sharpe', 0)
                elif self.metric == 'return':
                    return result.get('total_return', 0)
                elif self.metric == 'win_rate':
                    return result.get('win_rate', 0)
                    
        except Exception as e:
            return 0
            
        return 0


def run_bayesian_optimization(df: pd.DataFrame, indicator: str, 
                             n_trials: int = 100,
                             backtest_engine=None) -> Dict:
    """
    運行 Bayesian 優化
    
    Example:
        best_params = run_bayesian_optimization(df, 'RSI', n_trials=100)
    """
    optimizer = BayesianOptimizer(n_trials=n_trials, direction='maximize')
    optimizer.create_study(f"{indicator}_optimization")
    
    objective = IndicatorObjective(df, indicator, 'sharpe', backtest_engine)
    optimizer.optimize(objective)
    
    return {
        'best_params': optimizer.get_best_params(),
        'best_value': optimizer.study.best_value,
        'top_10': optimizer.get_top_n(10)
    }


# Test
if __name__ == "__main__":
    import yfinance as yf
    from src.backtest.engine import BacktestEngine
    
    print("=== Optuna Bayesian Optimization Test ===")
    
    # Fetch data
    ticker = yf.Ticker('NVDA')
    df = ticker.history(start='2024-01-01', end='2025-01-01')
    df = df.reset_index()
    df = df.rename(columns={'Date': 'date', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'})
    
    # Setup backtest engine
    engine = BacktestEngine('./config')
    
    # Run optimization (reduced trials for test)
    print("Optimizing RSI...")
    result = run_bayesian_optimization(df, 'RSI', n_trials=20, backtest_engine=engine)
    
    print(f"\n=== Best Result ===")
    print(f"Best params: {result['best_params']}")
    print(f"Best Sharpe: {result['best_value']:.2f}")
    
    print("\n🎉 Bayesian optimization working!")
