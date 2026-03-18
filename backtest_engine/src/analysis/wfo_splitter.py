"""
Walk-Forward Optimization (WFO) Data Splitter

Splits a historical dataset into In-Sample (Train) and Out-of-Sample (Test)
to prevent overfitting when evaluating 8,864 strategy parameter combinations.
"""
from __future__ import annotations

from typing import Tuple, Optional
import pandas as pd


class DataSplitter:
    """
    Handles time-based splitting of market data for Walk-Forward Optimization.
    """

    def __init__(self, train_ratio: float = 0.7):
        """
        Args:
            train_ratio: The proportion of data to use for training (In-Sample).
                         Default 0.7 means 70% Train, 30% Test.
        """
        if not (0.0 < train_ratio < 1.0):
            raise ValueError("train_ratio must be between 0.0 and 1.0")
        self.train_ratio = train_ratio

    def split_by_date(
        self,
        df: pd.DataFrame,
        date_col: str = "date",
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Splits a DataFrame chronologically into train and test sets without look-ahead bias.

        Args:
            df: The market data DataFrame.
            date_col: Name of the date/datetime column. If data is indexed by date, set to None.

        Returns:
            Tuple of (Train DataFrame, Test DataFrame)
        """
        if df.empty:
            return pd.DataFrame(), pd.DataFrame()

        df_sorted = df.copy()
        
        # Sort chronologically to prevent look-ahead bias
        if date_col and date_col in df_sorted.columns:
            df_sorted = df_sorted.sort_values(by=date_col)
        else:
            df_sorted = df_sorted.sort_index()

        split_idx = int(len(df_sorted) * self.train_ratio)

        train_df = df_sorted.iloc[:split_idx].copy()
        test_df = df_sorted.iloc[split_idx:].copy()

        return train_df, test_df

    def split_by_specific_date(
        self, 
        df: pd.DataFrame, 
        split_date: str,
        date_col: str = "date"
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Splits data according to an exact date boundary.
        
        Args:
            df: The market data DataFrame.
            split_date: The date string (e.g., '2023-01-01') where Test data begins (inclusive).
            date_col: Name of the date/datetime column.
            
        Returns:
            Tuple of (Train DataFrame, Test DataFrame)
        """
        if df.empty:
            return pd.DataFrame(), pd.DataFrame()
            
        df_sorted = df.copy()
        split_dt = pd.to_datetime(split_date)
        
        if date_col and date_col in df_sorted.columns:
            df_sorted[date_col] = pd.to_datetime(df_sorted[date_col])
            train_df = df_sorted[df_sorted[date_col] < split_dt].copy()
            test_df = df_sorted[df_sorted[date_col] >= split_dt].copy()
            train_df = train_df.sort_values(by=date_col)
            test_df = test_df.sort_values(by=date_col)
        else:
            df_sorted.index = pd.to_datetime(df_sorted.index)
            train_df = df_sorted[df_sorted.index < split_dt].copy()
            test_df = df_sorted[df_sorted.index >= split_dt].copy()
            train_df = train_df.sort_index()
            test_df = test_df.sort_index()
            
        return train_df, test_df


if __name__ == "__main__":
    # Quick Test
    dates = pd.date_range("2020-01-01", "2023-12-31", freq="D")
    mock_df = pd.DataFrame({"close": range(len(dates))}, index=dates)
    
    splitter = DataSplitter(train_ratio=0.7)
    train, test = splitter.split_by_date(mock_df, date_col=None)
    
    print(f"Total rows: {len(mock_df)}")
    print(f"Train rows: {len(train)} (ends {train.index[-1]})")
    print(f"Test rows: {len(test)} (starts {test.index[0]})")
