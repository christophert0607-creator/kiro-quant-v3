"""
Data Quality Validator - Anti-Survivorship Bias Rules
"""

import polars as pl
from typing import Dict, List, Optional
from datetime import datetime, timedelta


class DataQualityValidator:
    """數據質量驗證器 - 防止 Survivorship Bias"""
    
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {
            'max_missing_pct': 0.05,  # 5% missing data
            'max_gap_pct': 0.20,       # 20% price gap
            'min_data_points': 30,      # Minimum days required
        }
        
    def validate(self, df: pl.DataFrame, symbol: str) -> Dict:
        """
        驗證數據質量
        
        Returns:
            Dict with 'is_valid', 'issues', 'quality_score'
        """
        issues = []
        quality_score = 1.0
        
        # 1. Check minimum data points
        if len(df) < self.config['min_data_points']:
            issues.append(f"Too few data points: {len(df)} < {self.config['min_data_points']}")
            quality_score -= 0.5
            
        # 2. Check for missing values
        missing_pct = self._calculate_missing_pct(df)
        if missing_pct > self.config['max_missing_pct']:
            issues.append(f"Too much missing data: {missing_pct:.1%}")
            quality_score -= 0.3
            
        # 3. Check for price gaps
        gaps = self._detect_price_gaps(df)
        if gaps > self.config['max_gap_pct']:
            issues.append(f"Large price gaps detected: {gaps:.1%}")
            quality_score -= 0.2
            
        # 4. Check for survivorship bias indicators
        sb_issues = self._check_survivorship_bias(df)
        issues.extend(sb_issues)
        
        return {
            'is_valid': len(issues) == 0,
            'issues': issues,
            'quality_score': max(0, quality_score),
            'missing_pct': missing_pct,
            'gap_pct': gaps
        }
    
    def _calculate_missing_pct(self, df: pl.DataFrame) -> float:
        """計算缺失數據百分比"""
        total_cells = df.height * (df.width - 1)  # Exclude date column
        missing_cells = 0
        
        for col in df.columns:
            if col != 'date':
                missing_cells += df[col].null_count()
                
        return missing_cells / total_cells if total_cells > 0 else 0
    
    def _detect_price_gaps(self, df: pl.DataFrame) -> float:
        """檢測價格跳空"""
        if 'close' not in df.columns or df.height < 2:
            return 0
            
        prices = df['close'].to_numpy()
        gaps = []
        
        for i in range(1, len(prices)):
            if not (prices[i-1] > 0 and prices[i] > 0):
                continue
            pct_change = abs(prices[i] - prices[i-1]) / prices[i-1]
            gaps.append(pct_change)
            
        # Return max gap
        return max(gaps) if gaps else 0
    
    def _check_survivorship_bias(self, df: pl.DataFrame) -> List[str]:
        """檢測生存者偏差指標"""
        issues = []
        
        if 'close' not in df.columns:
            return issues
            
        # Check for suspicious patterns
        prices = df['close'].to_numpy()
        
        # Pattern 1: Too many zero returns at end (possible delisting)
        returns = []
        for i in range(1, len(prices)):
            if prices[i-1] > 0:
                ret = (prices[i] - prices[i-1]) / prices[i-1]
                returns.append(ret)
                
        if len(returns) > 0:
            # Check last 10% of returns for anomalies
            tail_size = max(1, len(returns) // 10)
            tail_returns = returns[-tail_size:]
            
            # If many zero/very small returns at end, might be delisted
            zero_returns = sum(1 for r in tail_returns if abs(r) < 0.001)
            if zero_returns > tail_size * 0.5:
                issues.append("Possible delisting detected (many zero returns at end)")
                
        return issues
    
    def filter_train_data(self, df: pl.DataFrame, train_end_date: str) -> pl.DataFrame:
        """
        Filter data for training period
        訓練期只使用存活既股票
        """
        train_end = pl.date(
            int(train_end_date[:4]),
            int(train_end_date[5:7]),
            int(train_end_date[8:10])
        )
        
        # Keep only data up to train end
        df = df.with_columns(pl.col('date').cast(pl.Date))
        
        return df.filter(pl.col('date') <= train_end)
    
    def filter_test_data(self, df: pl.DataFrame, test_start_date: str) -> pl.DataFrame:
        """
        Filter data for test period
        測試期使用所有股票 (包括退市)
        """
        test_start = pl.date(
            int(test_start_date[:4]),
            int(test_start_date[5:7]),
            int(test_start_date[8:10])
        )
        
        df = df.with_columns(pl.col('date').cast(pl.Date))
        
        return df.filter(pl.col('date') >= test_start)


class DataValidator:
    """統一驗證接口"""
    
    def __init__(self):
        self.validator = DataQualityValidator()
        
    def validate_dataset(self, df: pl.DataFrame, symbol: str) -> Dict:
        """驗證整個數據集"""
        return self.validator.validate(df, symbol)
    
    def should_use_for_train(self, df: pl.DataFrame, symbol: str) -> bool:
        """判斷是否應該用於訓練"""
        result = self.validator.validate(df, symbol)
        
        # For training, we want high quality data (surviving stocks)
        return result['is_valid'] and result['quality_score'] > 0.7
    
    def should_use_for_test(self, df: pl.DataFrame, symbol: str) -> bool:
        """判斷是否應該用於測試"""
        result = self.validator.validate(df, symbol)
        
        # For testing, we allow more flexibility (including delisted)
        return result['quality_score'] > 0.3
