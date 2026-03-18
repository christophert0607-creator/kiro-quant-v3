"""
XGBoost Hyperparameter Tuning
"""

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import accuracy_score
import yfinance as yf


STOCKS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD', 'INTC', 'NFLX',
          'IBM', 'ORCL', 'CSCO', 'CRM', 'ADBE', 'PYPL', 'SHOP', 'UBER', 'LYFT', 'BA',
          'CAT', 'GE', 'MMM', 'HON', 'UPS', 'LMT', 'RTX', 'NOC', 'GM', 'JPM',
          'BAC', 'WFC', 'C', 'GS', 'MS', 'AXP', 'V', 'MA', 'COIN', 'DIS',
          'CMCSA', 'T', 'VZ', 'TMUS', 'CHTR', 'WBD', 'EA', 'MSFT']


def prepare_features(close, volume):
    features = pd.DataFrame(index=close.index)
    
    for i in [1, 2, 3, 5, 10]:
        features[f'return_{i}d'] = close.pct_change(i)
        
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    features['rsi'] = 100 - (100 / (1 + rs))
    
    features['sma_5'] = close.rolling(5).mean()
    features['sma_20'] = close.rolling(20).mean()
    features['sma_ratio'] = features['sma_5'] / (features['sma_20'] + 1e-10)
    features['sma_50'] = close.rolling(50).mean()
    features['sma_50_ratio'] = close / (features['sma_50'] + 1e-10)
    
    features['volatility_10'] = close.pct_change().rolling(10).std()
    features['volatility_20'] = close.pct_change().rolling(20).std()
    
    features['volume_ratio'] = volume / (volume.rolling(20).mean() + 1e-10)
    
    features['momentum_5'] = close / (close.shift(5) + 1e-10) - 1
    features['momentum_10'] = close / (close.shift(10) + 1e-10) - 1
    features['momentum_20'] = close / (close.shift(20) + 1e-10) - 1
    
    ema12, ema26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
    features['macd'] = ema12 - ema26
    features['macd_signal'] = features['macd'].ewm(span=9).mean()
    features['macd_hist'] = features['macd'] - features['macd_signal']
    
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    features['bb_upper'] = (sma20 + 2*std20) / close
    features['bb_lower'] = (sma20 - 2*std20) / close
    
    return features


def create_labels(close, threshold=0.015):
    future_return = close.shift(-5) / close - 1
    labels = pd.Series(0, index=close.index)
    labels[future_return > threshold] = 1
    labels[future_return < -threshold] = -1
    return labels


# Load data once
print("Loading data...")
all_X, all_y = [], []
for symbol in STOCKS[:30]:
    try:
        data = yf.download(symbol, period='2y', progress=False)
        if len(data) < 200:
            continue
        if isinstance(data.columns, pd.MultiIndex):
            data = data.droplevel(1, axis=1)
        close = data['Close'].squeeze()
        volume = data['Volume'].squeeze() if 'Volume' in data.columns else pd.Series(1, index=close.index)
        features = prepare_features(close, volume)
        labels = create_labels(close)
        valid_idx = ~(features.isna().any(axis=1) | labels.isna())
        all_X.append(features[valid_idx])
        all_y.append(labels[valid_idx])
    except:
        pass

X = pd.concat(all_X, ignore_index=True)
y = pd.concat(all_y, ignore_index=True)
print(f"Total: {len(X)} samples")

split_idx = int(len(X) * 0.8)
X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
y_train_m = y_train.map({-1: 0, 0: 1, 1: 2})
y_test_m = y_test.map({-1: 0, 0: 1, 1: 2})


# Grid search
print("\n=== Hyperparameter Search ===")
best_acc = 0
best_params = {}

params_grid = [
    {'n_estimators': 100, 'max_depth': 4, 'learning_rate': 0.1},
    {'n_estimators': 150, 'max_depth': 5, 'learning_rate': 0.05},
    {'n_estimators': 200, 'max_depth': 6, 'learning_rate': 0.03},
    {'n_estimators': 250, 'max_depth': 7, 'learning_rate': 0.02},
    {'n_estimators': 100, 'max_depth': 8, 'learning_rate': 0.1},
    {'n_estimators': 300, 'max_depth': 5, 'learning_rate': 0.01},
    {'n_estimators': 150, 'max_depth': 4, 'learning_rate': 0.08},
    {'n_estimators': 200, 'max_depth': 3, 'learning_rate': 0.05},
]

for params in params_grid:
    model = xgb.XGBClassifier(
        n_estimators=params['n_estimators'],
        max_depth=params['max_depth'],
        learning_rate=params['learning_rate'],
        objective='multi:softmax',
        num_class=3,
        verbosity=0,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=3
    )
    model.fit(X_train, y_train_m)
    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test_m, y_pred)
    print(f"n={params['n_estimators']:3d}, d={params['max_depth']}, lr={params['learning_rate']:.2f} => Accuracy: {acc:.2%}")
    
    if acc > best_acc:
        best_acc = acc
        best_params = params

print(f"\n=== Best ===")
print(f"Accuracy: {best_acc:.2%}")
print(f"Params: {best_params}")
