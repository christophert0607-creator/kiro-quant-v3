"""
XGBoost Final - Best params + more features
"""

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.metrics import accuracy_score, classification_report
import yfinance as yf


STOCKS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD', 'INTC', 'NFLX',
          'IBM', 'ORCL', 'CSCO', 'CRM', 'ADBE', 'PYPL', 'SHOP', 'UBER', 'LYFT', 'BA',
          'CAT', 'GE', 'MMM', 'HON', 'UPS', 'LMT', 'RTX', 'NOC', 'GM', 'JPM',
          'BAC', 'WFC', 'C', 'GS', 'MS', 'AXP', 'V', 'MA', 'COIN', 'DIS',
          'CMCSA', 'T', 'VZ', 'TMUS', 'CHTR', 'WBD', 'EA', 'MSFT', 'AAPL', 'GOOGL']


def prepare_features(close, high, low, volume):
    features = pd.DataFrame(index=close.index)
    
    # Returns
    for i in [1, 2, 3, 5, 10, 20]:
        features[f'return_{i}d'] = close.pct_change(i)
        
    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    features['rsi'] = 100 - (100 / (1 + rs))
    features['rsi_7'] = delta.where(delta > 0, 0).rolling(7).mean() / (delta.where(delta < 0, 0).rolling(7).mean().abs() + 1e-10)
    
    # MAs
    for w in [5, 10, 20, 50, 100, 200]:
        features[f'sma_{w}'] = close.rolling(w).mean()
        features[f'sma_{w}_ratio'] = close / (features[f'sma_{w}'] + 1e-10)
    
    # Volatility
    for w in [5, 10, 20]:
        features[f'volatility_{w}'] = close.pct_change().rolling(w).std()
    
    # Volume
    features['volume_ratio'] = volume / (volume.rolling(20).mean() + 1e-10)
    features['volume_ma5'] = volume.rolling(5).mean()
    
    # Momentum
    for w in [5, 10, 20]:
        features[f'momentum_{w}'] = close / (close.shift(w) + 1e-10) - 1
    
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
    features['bb_width'] = (features['bb_upper'] - features['bb_lower']) / sma20 * close
    
    # Price position
    features['high_20_ratio'] = high.rolling(20).max() / (close + 1e-10)
    features['low_20_ratio'] = low.rolling(20).min() / (close + 1e-10)
    
    # ATR
    high_low = high - low
    high_close = (high - close.shift(1)).abs()
    low_close = (low - close.shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    features['atr'] = tr.rolling(14).mean()
    features['atr_ratio'] = features['atr'] / (close + 1e-10)
    
    return features


def create_labels(close, threshold=0.01):  # Lower threshold = more signals
    future_return = close.shift(-5) / close - 1
    labels = pd.Series(0, index=close.index)
    labels[future_return > threshold] = 1
    labels[future_return < -threshold] = -1
    return labels


# Load data
print("Loading data...")
all_X, all_y = [], []
for symbol in STOCKS:
    try:
        data = yf.download(symbol, period='2y', progress=False)
        if len(data) < 200:
            continue
        if isinstance(data.columns, pd.MultiIndex):
            data = data.droplevel(1, axis=1)
        close = data['Close'].squeeze()
        high = data['High'].squeeze() if 'High' in data.columns else close
        low = data['Low'].squeeze() if 'Low' in data.columns else close
        volume = data['Volume'].squeeze() if 'Volume' in data.columns else pd.Series(1, index=close.index)
        
        features = prepare_features(close, high, low, volume)
        labels = create_labels(close, threshold=0.01)
        
        valid_idx = ~(features.isna().any(axis=1) | labels.isna())
        if valid_idx.sum() > 100:
            all_X.append(features[valid_idx])
            all_y.append(labels[valid_idx])
            print(f"  {symbol}: {valid_idx.sum()}")
    except Exception as e:
        pass

X = pd.concat(all_X, ignore_index=True)
y = pd.concat(all_y, ignore_index=True)
print(f"\nTotal: {len(X)} samples")
print(f"Labels: BUY={sum(y==1)}, SELL={sum(y==-1)}, HOLD={sum(y==0)}")

# Train
split_idx = int(len(X) * 0.8)
X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
y_train_m = y_train.map({-1: 0, 0: 1, 1: 2})
y_test_m = y_test.map({-1: 0, 0: 1, 1: 2})

# Best model
model = xgb.XGBClassifier(
    n_estimators=300,
    max_depth=5,
    learning_rate=0.01,
    objective='multi:softmax',
    num_class=3,
    verbosity=0,
    subsample=0.8,
    colsample_bytree=0.8,
    min_child_weight=3,
    gamma=0.1
)
model.fit(X_train, y_train_m)
y_pred = model.predict(X_test)
acc = accuracy_score(y_test_m, y_pred)

print(f"\n{'='*50}")
print(f"XGBoost Final Results")
print(f"{'='*50}")
print(f"Accuracy: {acc:.2%}")
print(f"Features: {X.shape[1]}")

# Feature importance
fi = dict(zip(X.columns, model.feature_importances_))
print("\nTop 15 Features:")
for i, (feat, imp) in enumerate(sorted(fi.items(), key=lambda x: x[1], reverse=True)[:15]):
    print(f"  {i+1}. {feat}: {imp:.3f}")
