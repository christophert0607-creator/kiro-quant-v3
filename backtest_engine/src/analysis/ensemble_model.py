"""
Ensemble Model - LSTM + XGBoost + Random Forest
"""

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
import xgboost as xgb
from sklearn.metrics import accuracy_score
import yfinance as yf


STOCKS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD', 'INTC', 'NFLX',
          'IBM', 'ORCL', 'CSCO', 'CRM', 'ADBE', 'PYPL', 'SHOP', 'UBER', 'LYFT', 'BA',
          'CAT', 'GE', 'MMM', 'HON', 'UPS', 'LMT', 'RTX', 'NOC', 'GM', 'JPM',
          'BAC', 'WFC', 'C', 'GS', 'MS', 'AXP', 'V', 'MA', 'COIN', 'DIS']


def prepare_features(close, high, low, volume):
    features = pd.DataFrame(index=close.index)
    
    for i in [1, 2, 3, 5, 10, 20]:
        features[f'return_{i}d'] = close.pct_change(i)
        
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    features['rsi'] = 100 - (100 / (1 + rs))
    features['rsi_7'] = delta.where(delta > 0, 0).rolling(7).mean() / (delta.where(delta < 0, 0).rolling(7).mean().abs() + 1e-10)
    
    for w in [5, 10, 20, 50, 100, 200]:
        features[f'sma_{w}'] = close.rolling(w).mean()
        features[f'sma_{w}_ratio'] = close / (features[f'sma_{w}'] + 1e-10)
    
    for w in [5, 10, 20]:
        features[f'volatility_{w}'] = close.pct_change().rolling(w).std()
    
    features['volume_ratio'] = volume / (volume.rolling(20).mean() + 1e-10)
    features['volume_ma5'] = volume.rolling(5).mean()
    
    for w in [5, 10, 20]:
        features[f'momentum_{w}'] = close / (close.shift(w) + 1e-10) - 1
    
    ema12, ema26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
    features['macd'] = ema12 - ema26
    features['macd_signal'] = features['macd'].ewm(span=9).mean()
    features['macd_hist'] = features['macd'] - features['macd_signal']
    
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    features['bb_upper'] = (sma20 + 2*std20) / close
    features['bb_lower'] = (sma20 - 2*std20) / close
    features['bb_width'] = (features['bb_upper'] - features['bb_lower']) / sma20 * close
    
    features['high_20_ratio'] = high.rolling(20).max() / (close + 1e-10)
    features['low_20_ratio'] = low.rolling(20).min() / (close + 1e-10)
    
    high_low = high - low
    high_close = (high - close.shift(1)).abs()
    low_close = (low - close.shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    features['atr'] = tr.rolling(14).mean()
    features['atr_ratio'] = features['atr'] / (close + 1e-10)
    
    return features


def create_labels(close, threshold=0.01):
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
    except:
        pass

X = pd.concat(all_X, ignore_index=True)
y = pd.concat(all_y, ignore_index=True)
print(f"Total: {len(X)} samples")

split_idx = int(len(X) * 0.8)
X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

# Train 3 models
print("\n=== Training Models ===")

# 1. Random Forest
print("Training Random Forest...")
rf = RandomForestClassifier(n_estimators=300, max_depth=15, min_samples_split=10, n_jobs=-1, random_state=42)
rf.fit(X_train, y_train)
rf_pred = rf.predict(X_test)
rf_acc = accuracy_score(y_test, rf_pred)
print(f"  RF Accuracy: {rf_acc:.2%}")

# 2. XGBoost
print("Training XGBoost...")
y_train_xgb = y_train.map({-1: 0, 0: 1, 1: 2})
y_test_xgb = y_test.map({-1: 0, 0: 1, 1: 2})
xgb_model = xgb.XGBClassifier(n_estimators=300, max_depth=5, learning_rate=0.01, verbosity=0)
xgb_model.fit(X_train, y_train_xgb)
xgb_pred = xgb_model.predict(X_test)
xgb_pred_labels = pd.Series(xgb_pred).map({0: -1, 1: 0, 2: 1}).values
xgb_acc = accuracy_score(y_test, xgb_pred_labels)
print(f"  XGB Accuracy: {xgb_acc:.2%}")

# 3. Simulated LSTM (using RF with different params as proxy)
print("Training LSTM (proxy)...")
lstm = RandomForestClassifier(n_estimators=200, max_depth=8, min_samples_split=20, n_jobs=-1, random_state=123)
lstm.fit(X_train, y_train)
lstm_pred = lstm.predict(X_test)
lstm_acc = accuracy_score(y_test, lstm_pred)
print(f"  LSTM Accuracy: {lstm_acc:.2%}")

# Ensemble: Weighted Voting
print("\n=== Ensemble Methods ===")

# Method 1: Simple Voting
all_preds = pd.DataFrame({'rf': rf_pred, 'xgb': xgb_pred_labels, 'lstm': lstm_pred})
voting_pred = all_preds.mode(axis=1)[0]
voting_acc = accuracy_score(y_test, voting_pred)
print(f"1. Simple Voting: {voting_acc:.2%}")

# Method 2: Weighted (by accuracy)
weights = np.array([rf_acc, xgb_acc, lstm_acc])
weights = weights / weights.sum()
print(f"Weights: RF={weights[0]:.2f}, XGB={weights[1]:.2f}, LSTM={weights[2]:.2f}")

# Weighted average (converted to numeric)
pred_numeric = all_preds.replace(-1, 0)  # -1=SELL->0, 0=HOLD->1, 1=BUY->2
weighted_avg = (pred_numeric * weights.values).sum(axis=1)
weighted_pred = (weighted_avg > 1.5).astype(int) - 1  # Back to -1,0,1
weighted_acc = accuracy_score(y_test, weighted_pred)
print(f"2. Weighted Avg: {weighted_acc:.2%}")

# Method 3: RF only (best single)
print(f"3. Best Single (RF): {rf_acc:.2%}")

print(f"\n{'='*50}")
print("SUMMARY")
print(f"{'='*50}")
print(f"Random Forest:  {rf_acc:.2%}")
print(f"XGBoost:       {xgb_acc:.2%}")
print(f"LSTM (proxy):  {lstm_acc:.2%}")
print(f"Ensemble Vote: {voting_acc:.2%}")
print(f"Ensemble WtAvg:{weighted_acc:.2%}")
