"""
XGBoost - 50 Stocks, 500 Days Training
"""

import pandas as pd
import numpy as np
import xgboost as xgb
from typing import Dict
from sklearn.metrics import accuracy_score
import yfinance as yf


# 50 popular stocks
STOCKS = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD', 'INTC', 'NFLX',
    'IBM', 'ORCL', 'CSCO', 'CRM', 'ADBE', 'PYPL', 'SQ', 'SHOP', 'UBER', 'LYFT',
    'BA', 'CAT', 'GE', 'MMM', 'HON', 'UPS', 'LMT', 'RTX', 'NOC', 'GM',
    'JPM', 'BAC', 'WFC', 'C', 'GS', 'MS', 'AXP', 'V', 'MA', 'COIN',
    'DIS', 'CMCSA', 'T', 'VZ', 'TMUS', 'CHTR', 'PARA', 'WBD', 'NFLX', 'EA'
]


class XGBoostClassifier:
    def __init__(self, n_estimators=150, max_depth=6, learning_rate=0.05):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.model = None
        self.feature_cols = []
        
    def prepare_features(self, close, volume):
        features = pd.DataFrame(index=close.index)
        
        for i in [1, 2, 3, 5, 10]:
            features[f'return_{i}d'] = close.pct_change(i)
            
        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        features['rsi'] = 100 - (100 / (1 + rs))
        
        # MAs
        features['sma_5'] = close.rolling(5).mean()
        features['sma_20'] = close.rolling(20).mean()
        features['sma_ratio'] = features['sma_5'] / (features['sma_20'] + 1e-10)
        features['sma_50'] = close.rolling(50).mean()
        features['sma_50_ratio'] = close / (features['sma_50'] + 1e-10)
        
        # Volatility
        features['volatility_10'] = close.pct_change().rolling(10).std()
        features['volatility_20'] = close.pct_change().rolling(20).std()
        
        # Volume
        features['volume_ratio'] = volume / (volume.rolling(20).mean() + 1e-10)
        
        # Momentum
        features['momentum_5'] = close / (close.shift(5) + 1e-10) - 1
        features['momentum_10'] = close / (close.shift(10) + 1e-10) - 1
        features['momentum_20'] = close / (close.shift(20) + 1e-10) - 1
        
        # MACD
        ema12, ema26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
        features['macd'] = ema12 - ema26
        features['macd_signal'] = features['macd'].ewm(span=9).mean()
        features['macd_hist'] = features['macd'] - features['macd_signal']
        
        # Bollinger
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        features['bb_upper'] = (sma20 + 2*std20) / close
        features['bb_lower'] = (sma20 - 2*std20) / close
        
        self.feature_cols = features.columns.tolist()
        return features
    
    def create_labels(self, close, threshold=0.015):
        future_return = close.shift(-5) / close - 1
        labels = pd.Series(0, index=close.index)
        labels[future_return > threshold] = 1
        labels[future_return < -threshold] = -1
        return labels
    
    def train(self, stocks, period='2y') -> Dict:
        all_X, all_y = [], []
        
        print(f"Loading {len(stocks)} stocks ({period})...")
        for i, symbol in enumerate(stocks):
            try:
                data = yf.download(symbol, period=period, progress=False)
                if len(data) < 200:
                    print(f"  [{i+1}/{len(stocks)}] {symbol}: SKIP (only {len(data)} rows)")
                    continue
                    
                if isinstance(data.columns, pd.MultiIndex):
                    data = data.droplevel(1, axis=1)
                
                close = data['Close'].squeeze()
                volume = data['Volume'].squeeze() if 'Volume' in data.columns else pd.Series(1, index=close.index)
                
                features = self.prepare_features(close, volume)
                labels = self.create_labels(close)
                
                valid_idx = ~(features.isna().any(axis=1) | labels.isna())
                all_X.append(features[valid_idx])
                all_y.append(labels[valid_idx])
                print(f"  [{i+1}/{len(stocks)}] {symbol}: {valid_idx.sum()} samples ✓")
            except Exception as e:
                print(f"  [{i+1}/{len(stocks)}] {symbol}: ERROR - {e}")
        
        X = pd.concat(all_X, ignore_index=True)
        y = pd.concat(all_y, ignore_index=True)
        
        print(f"\nTotal: {len(X)} samples")
        print(f"Label dist: BUY={sum(y==1)}, SELL={sum(y==-1)}, HOLD={sum(y==0)}")
        
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
        
        y_train_m = y_train.map({-1: 0, 0: 1, 1: 2})
        y_test_m = y_test.map({-1: 0, 0: 1, 1: 2})
        
        self.model = xgb.XGBClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            objective='multi:softmax',
            num_class=3,
            verbosity=0,
            subsample=0.8,
            colsample_bytree=0.8
        )
        self.model.fit(X_train, y_train_m)
        
        y_pred = self.model.predict(X_test)
        accuracy = accuracy_score(y_test_m, y_pred)
        
        return {
            "accuracy": accuracy,
            "total_samples": len(X),
            "train_size": len(X_train),
            "test_size": len(X_test),
            "feature_importance": dict(zip(self.feature_cols, self.model.feature_importances_.tolist()))
        }


if __name__ == "__main__":
    model = XGBoostClassifier(n_estimators=150, max_depth=6, learning_rate=0.05)
    result = model.train(STOCKS)
    
    print(f"\n{'='*50}")
    print(f"50 Stocks XGBoost Results")
    print(f"{'='*50}")
    print(f"Accuracy: {result['accuracy']:.2%}")
    print(f"Total samples: {result['total_samples']}")
    
    print("\nTop 10 Features:")
    for i, (feat, imp) in enumerate(sorted(result['feature_importance'].items(), key=lambda x: x[1], reverse=True)[:10]):
        print(f"  {i+1}. {feat}: {imp:.3f}")
