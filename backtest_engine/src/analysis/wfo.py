"""
Walk-Forward Optimization (WFO) & Analysis Module
"""

import polars as pl
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import json


@dataclass
class BacktestResult:
    """單次回測結果"""
    indicator: str
    params: Dict
    train_metrics: Dict
    test_metrics: Optional[Dict] = None
    equity_curve: Optional[List[float]] = None
    trades: Optional[List[Dict]] = None


@dataclass
class PerformanceMetrics:
    """績效指標"""
    total_return: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    n_trades: int
    avg_trade: float
    
    def to_dict(self) -> Dict:
        return {
            'total_return': self.total_return,
            'sharpe_ratio': self.sharpe_ratio,
            'sortino_ratio': self.sortino_ratio,
            'calmar_ratio': self.calmar_ratio,
            'max_drawdown': self.max_drawdown,
            'win_rate': self.win_rate,
            'profit_factor': self.profit_factor,
            'n_trades': self.n_trades,
            'avg_trade': self.avg_trade
        }


class DataSplitter:
    """數據分割器 - Train/Validation/Test"""
    
    def __init__(self, train_pct: float = 0.7, val_pct: float = 0.15):
        """
        Args:
            train_pct: 訓練集百分比
            val_pct: 驗證集百分比
            (Test 自動 = 1 - train_pct - val_pct)
        """
        self.train_pct = train_pct
        self.val_pct = val_pct
        self.test_pct = 1.0 - train_pct - val_pct
        
    def split(self, df: pl.DataFrame, date_col: str = 'date') -> Tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
        """
        分割數據
        
        Returns:
            (train_df, val_df, test_df)
        """
        n = len(df)
        train_size = int(n * self.train_pct)
        val_size = int(n * self.val_pct)
        
        train_df = df.head(train_size)
        val_df = df.slice(train_size, val_size)
        test_df = df.tail(n - train_size - val_size)
        
        return train_df, val_df, test_df
    
    def get_date_ranges(self, df: pl.DataFrame, date_col: str = 'date') -> Dict[str, str]:
        """獲取各個period既日期範圍"""
        dates = df[date_col].to_list()
        
        n = len(dates)
        train_size = int(n * self.train_pct)
        val_size = int(n * self.val_pct)
        
        return {
            'train': {'start': dates[0], 'end': dates[train_size-1]},
            'val': {'start': dates[train_size], 'end': dates[train_size + val_size - 1]},
            'test': {'start': dates[train_size + val_size], 'end': dates[-1]}
        }


class MetricsCalculator:
    """績效指標計算器"""
    
    @staticmethod
    def calculate(equity_curve: List[float], trades: List[float]) -> PerformanceMetrics:
        """
        計算所有績效指標
        
        Args:
            equity_curve: 資金曲線
            trades: 每筆交易既盈虧
            
        Returns:
            PerformanceMetrics
        """
        if not equity_curve or len(equity_curve) < 2:
            return PerformanceMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0)
            
        equity = np.array(equity_curve)
        returns = np.diff(equity) / equity[:-1]
        returns = returns[~np.isnan(returns)]
        
        # Total Return
        total_return = (equity[-1] - equity[0]) / equity[0] if equity[0] > 0 else 0
        
        # Sharpe Ratio
        sharpe = MetricsCalculator._sharpe(returns)
        
        # Sortino Ratio (只用 downside risk)
        sortino = MetricsCalculator._sortino(returns)
        
        # Calmar Ratio (return / max_dd)
        max_dd = MetricsCalculator._max_drawdown(equity)
        calmar = total_return / max_dd if max_dd > 0 else 0
        
        # Win Rate
        wins = [t for t in trades if t > 0]
        win_rate = len(wins) / len(trades) if trades else 0
        
        # Profit Factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Avg Trade
        avg_trade = sum(trades) / len(trades) if trades else 0
        
        return PerformanceMetrics(
            total_return=total_return,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            max_drawdown=max_dd,
            win_rate=win_rate,
            profit_factor=profit_factor,
            n_trades=len(trades),
            avg_trade=avg_trade
        )
    
    @staticmethod
    def _sharpe(returns: np.ndarray, risk_free: float = 0.0) -> float:
        """計算 Sharpe Ratio"""
        if len(returns) < 2 or np.std(returns) == 0:
            return 0
        excess = returns - risk_free
        return np.mean(excess) / np.std(excess) * np.sqrt(252)
    
    @staticmethod
    def _sortino(returns: np.ndarray, risk_free: float = 0.0) -> float:
        """計算 Sortino Ratio"""
        if len(returns) < 2:
            return 0
        excess = returns - risk_free
        downside = returns[returns < 0]
        if len(downside) == 0 or np.std(downside) == 0:
            return 0
        return np.mean(excess) / np.std(downside) * np.sqrt(252)
    
    @staticmethod
    def _max_drawdown(equity: np.ndarray) -> float:
        """計算最大回撤"""
        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / peak
        return np.max(drawdown)


class WFOEngine:
    """Walk-Forward Optimization 引擎"""
    
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {
            'train_pct': 0.70,
            'val_pct': 0.15,
            'top_n': 10,  # Select top N for validation
            'min_sharpe': 0.5  # Minimum Sharpe to consider
        }
        self.splitter = DataSplitter(
            self.config['train_pct'],
            self.config['val_pct']
        )
        self.metrics = MetricsCalculator()
        self.results: List[BacktestResult] = []
        
    def run_all_combinations(self, df: pl.DataFrame, 
                           indicator_configs: List[Dict],
                           engine) -> List[BacktestResult]:
        """
        運行所有 8864 種組合
        
        Args:
            df: 完整數據
            indicator_configs: 指標配置 [{'name': 'RSI', 'params': [...]}, ...]
            engine: BacktestEngine 實例
            
        Returns:
            所有結果列表
        """
        # Split data
        train_df, val_df, test_df = self.splitter.split(df)
        
        print(f"Data split: Train={len(train_df)}, Val={len(val_df)}, Test={len(test_df)}")
        
        results = []
        
        # Generate all parameter combinations
        from src.backtest.engine import generate_param_combinations
        
        for config in indicator_configs:
            indicator_name = config['name']
            param_list = generate_param_combinations(indicator_name)
            
            for params in param_list:
                try:
                    # Train on training set
                    train_result = engine.run(train_df.to_pandas(), indicator_name, params, 'US')
                    train_metrics = self.metrics.calculate(
                        train_result['equity'],
                        train_result['trades']
                    )
                    
                    # Only keep if Sharpe > threshold
                    if train_metrics.sharpe_ratio < self.config['min_sharpe']:
                        continue
                    
                    result = BacktestResult(
                        indicator=indicator_name,
                        params=params,
                        train_metrics=train_metrics.to_dict()
                    )
                    results.append(result)
                    
                except Exception as e:
                    continue
        
        # Sort by Sharpe and select top N
        results.sort(key=lambda x: x.train_metrics['sharpe_ratio'], reverse=True)
        
        print(f"Total valid strategies: {len(results)}")
        
        return results[:self.config['top_n']]
    
    def validate_top_strategies(self, results: List[BacktestResult],
                               val_df: pl.DataFrame,
                               engine) -> List[BacktestResult]:
        """在 Validation 數據上驗證 Top N 策略"""
        validated = []
        
        for result in results:
            try:
                val_result = engine.run(val_df.to_pandas(), 
                                      result.indicator, 
                                      result.params, 'US')
                val_metrics = self.metrics.calculate(
                    val_result['equity'],
                    val_result['trades']
                )
                
                result.test_metrics = val_metrics.to_dict()
                validated.append(result)
                
            except Exception as e:
                continue
                
        return validated
    
    def final_test(self, results: List[BacktestResult],
                  test_df: pl.DataFrame,
                  engine) -> List[BacktestResult]:
        """在 Test 數據上進行最終測試"""
        tested = []
        
        for result in results:
            try:
                test_result = engine.run(test_df.to_pandas(),
                                        result.indicator,
                                        result.params, 'US')
                test_metrics = self.metrics.calculate(
                    test_result['equity'],
                    test_result['trades']
                )
                
                result.test_metrics = test_metrics.to_dict()
                result.equity_curve = test_result['equity']
                result.trades = test_result['trades']
                tested.append(result)
                
            except Exception as e:
                continue
                
        return tested


class OutputGenerator:
    """輸出生成器"""
    
    def __init__(self, output_dir: str = "./output"):
        self.output_dir = output_dir
        import os
        os.makedirs(output_dir, exist_ok=True)
        
    def save_summary(self, results: List[BacktestResult], filename: str = "strategy_summary.csv"):
        """保存策略摘要"""
        lines = ["indicator,params,train_sharpe,train_return,test_sharpe,test_return\n"]
        
        for r in results:
            params_str = "_".join([f"{k}={v}" for k, v in r.params.items()])
            train_s = r.train_metrics.get('sharpe_ratio', 0)
            train_r = r.train_metrics.get('total_return', 0)
            test_s = r.test_metrics.get('sharpe_ratio', 0) if r.test_metrics else 0
            test_r = r.test_metrics.get('total_return', 0) if r.test_metrics else 0
            
            lines.append(f"{r.indicator},{params_str},{train_s:.3f},{train_r:.3f},{test_s:.3f},{test_r:.3f}\n")
            
        with open(f"{self.output_dir}/{filename}", 'w') as f:
            f.writelines(lines)
            
    def save_top_strategies(self, results: List[BacktestResult], filename: str = "top_strategies.json"):
        """保存 Top 策略"""
        data = []
        for r in results:
            data.append({
                'indicator': r.indicator,
                'params': r.params,
                'train_metrics': r.train_metrics,
                'test_metrics': r.test_metrics
            })
            
        with open(f"{self.output_dir}/{filename}", 'w') as f:
            json.dump(data, f, indent=2)
            
    def generate_report(self, results: List[BacktestResult]) -> str:
        """生成文字報告"""
        report = []
        report.append("=" * 60)
        report.append("8×8×139 BACKTEST - FINAL REPORT")
        report.append("=" * 60)
        
        for i, r in enumerate(results[:10], 1):
            report.append(f"\n#{i} {r.indicator} {r.params}")
            report.append(f"   Train: Sharpe={r.train_metrics['sharpe_ratio']:.2f}, Return={r.train_metrics['total_return']:.2%}")
            if r.test_metrics:
                report.append(f"   Test:  Sharpe={r.test_metrics['sharpe_ratio']:.2f}, Return={r.test_metrics['total_return']:.2%}")
                
        return "\n".join(report)
