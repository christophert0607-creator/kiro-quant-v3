"""
Walk-Forward Optimization (WFO) Runner

This module orchestrates the train/test split execution for the 8,864 strategies.
It depends on the core backtest engine (Phase 2 & 3) to actually run the trades.
"""

from __future__ import annotations
import pandas as pd
from typing import List, Dict, Any

from .wfo_splitter import DataSplitter
from .metrics import compute_metrics


class WFORunner:
    """
    Manages the In-Sample (Train) and Out-of-Sample (Test) execution flow.
    """

    def __init__(self, train_ratio: float = 0.7, top_n_to_test: int = 10):
        self.splitter = DataSplitter(train_ratio=train_ratio)
        self.top_n_to_test = top_n_to_test
        self.in_sample_results: List[Dict[str, Any]] = []
        self.out_of_sample_results: List[Dict[str, Any]] = []

    def run_wfo(self, df: pd.DataFrame, strategy_params_list: List[Dict[str, Any]], engine_callable) -> None:
        """
        Executes the Walk-Forward Optimization pipeline.
        
        Args:
            df: Full historical market data.
            strategy_params_list: List of the 8,864 parameter combinations.
            engine_callable: A function/method that takes (df, params) and returns 
                             (equity_curve: pd.Series, returns: pd.Series).
        """
        if df.empty or not strategy_params_list:
            print("WFORunner: Empty data or strategy list.")
            return

        print(f"Starting WFO Pipeline. Total combinations: {len(strategy_params_list)}")
        
        # 1. Split Data
        train_df, test_df = self.splitter.split_by_date(df)
        print(f"Train Period: {train_df.index[0].date()} to {train_df.index[-1].date()}")
        print(f"Test Period:  {test_df.index[0].date()} to {test_df.index[-1].date()}")

        # 2. In-Sample (Training) Phase
        print(f"--- Running In-Sample Phase ({len(strategy_params_list)} strategies) ---")
        for i, params in enumerate(strategy_params_list):
            try:
                equity, returns = engine_callable(train_df, params)
                metrics = compute_metrics(equity, returns)
                
                # We need a scoring mechanism to rank them. Here we use Sharpe * Calmar as an example.
                score = metrics.get('sharpe_ratio', 0) * metrics.get('calmar_ratio', 0)
                
                self.in_sample_results.append({
                    "strategy_id": i,
                    "params": params,
                    "metrics": metrics,
                    "score": score
                })
            except Exception as e:
                # Log error and continue to next parameter set
                print(f"Error running strategy {i} in-sample: {e}")

        # 3. Select Top N Strategies from Training Phase
        # Sort descending by our custom score
        self.in_sample_results.sort(key=lambda x: x['score'], reverse=True)
        top_strategies = self.in_sample_results[:self.self.top_n_to_test]

        print(f"--- Running Out-of-Sample Phase (Top {len(top_strategies)} strategies) ---")
        
        # 4. Out-of-Sample (Testing) Phase
        for result in top_strategies:
            strat_id = result['strategy_id']
            params = result['params']
            try:
                equity, returns = engine_callable(test_df, params)
                test_metrics = compute_metrics(equity, returns)
                
                self.out_of_sample_results.append({
                    "strategy_id": strat_id,
                    "params": params,
                    "train_metrics": result['metrics'],
                    "test_metrics": test_metrics,
                    "train_score": result['score'],
                    # Calculate drop-off (degradation) between train and test
                    "sharpe_degradation": test_metrics.get('sharpe_ratio', 0) - result['metrics'].get('sharpe_ratio', 0)
                })
            except Exception as e:
                print(f"Error running strategy {strat_id} out-of-sample: {e}")

        print("WFO Pipeline Complete.")

    def export_results(self, output_dir: str = "output/summary/") -> None:
        """Exports the Out-of-Sample results to CSV."""
        import os
        os.makedirs(output_dir, exist_ok=True)
        
        if not self.out_of_sample_results:
            print("No out-of-sample results to export.")
            return

        # Flatten strictly the test metrics for reporting
        rows = []
        for res in self.out_of_sample_results:
            row = {
                "strategy_id": res["strategy_id"],
                "params": str(res["params"]),
                "train_score": res["train_score"],
                "sharpe_degradation": res["sharpe_degradation"]
            }
            # Flatten dicts
            for k, v in res["train_metrics"].items():
                row[f"train_{k}"] = v
            for k, v in res["test_metrics"].items():
                row[f"test_{k}"] = v
            rows.append(row)

        df_out = pd.DataFrame(rows)
        # Sort by best test performance (e.g., test test_annualized_return)
        if "test_annualized_return" in df_out.columns:
            df_out = df_out.sort_values(by="test_annualized_return", ascending=False)
            
        out_path = os.path.join(output_dir, "wfo_top_strategies.csv")
        df_out.to_csv(out_path, index=False)
        print(f"WFO results exported to {out_path}")


# Mock Engine Callable for basic validation
def _mock_engine(df, params):
    import numpy as np
    # Returns random walk logic just to test the WFO pipe
    returns = pd.Series(np.random.normal(0.0001, 0.01, len(df)), index=df.index)
    equity = (1 + returns).cumprod() * 100000
    return equity, returns

if __name__ == "__main__":
    dates = pd.date_range("2020-01-01", "2023-12-31", freq="B") # Business days
    mock_df = pd.DataFrame({"close": np.random.normal(100, 5, len(dates))}, index=dates)
    
    mock_params = [{"period": p} for p in range(5)] # Just 5 combos for testing
    
    runner = WFORunner(train_ratio=0.7, top_n_to_test=2)
    runner.run_wfo(mock_df, mock_params, _mock_engine)
    runner.export_results("./output/summary/")
