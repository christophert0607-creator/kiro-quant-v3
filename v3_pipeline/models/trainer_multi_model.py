#!/usr/bin/env python3
"""
Multi-Model Training System
========================
訓練多種唔同類型既模型：
1. LSTM (現有)
2. Random Forest
3. XGBoost
4. LightGBM
5. Logistic Regression
6. SVM

Author: Kiro Quant V3
"""

import argparse
import json
import logging
import sys
import os
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
import joblib
import torch
import torch.nn as nn

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("MultiModelTrainer")


# ============ LSTM Model ============

class LSTMModel(nn.Module):
    def __init__(self, input_dim, hidden_dim=64):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, batch_first=True, num_layers=2)
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, 32),
            nn.ReLU(),
            nn.Linear(32, 3)
        )
    
    def forward(self, x):
        _, (h, _) = self.lstm(x)
        return self.fc(h[-1])


# ============ Feature Engineering ============

def calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    """計算技術指標特徵"""
    close = df['Close'].values.flatten()
    high = df['High'].values.flatten()
    low = df['Low'].values.flatten()
    volume = df['Volume'].values.flatten()
    
    features = pd.DataFrame()
    
    # Price features
    close_series = pd.Series(close)
    for window in [5, 10, 20, 50]:
        features[f'sma_{window}'] = close_series.rolling(window).mean()
        features[f'ema_{window}'] = close_series.ewm(span=window).mean()
        features[f'return_{window}d'] = close_series.pct_change(window)
    
    # RSI
    delta = close_series.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    features['rsi_14'] = 100 - (100 / (1 + rs))
    
    # MACD
    ema12 = close_series.ewm(span=12).mean()
    ema26 = close_series.ewm(span=26).mean()
    features['macd'] = ema12 - ema26
    features['macd_signal'] = features['macd'].ewm(span=9).mean()
    
    # Bollinger Bands
    sma20 = close_series.rolling(20).mean()
    std20 = close_series.rolling(20).std()
    features['bb_upper'] = sma20 + 2 * std20
    features['bb_lower'] = sma20 - 2 * std20
    features['bb_width'] = (features['bb_upper'] - features['bb_lower']) / sma20
    
    # ATR
    high_series = pd.Series(high)
    low_series = pd.Series(low)
    high_low = high_series - low_series
    high_close = np.abs(high_series - close_series.shift())
    low_close = np.abs(low_series - close_series.shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    features['atr_14'] = tr.rolling(14).mean()
    
    # Volume features
    volume_series = pd.Series(volume)
    features['volume_sma_20'] = volume_series.rolling(20).mean()
    features['volume_ratio'] = volume / features['volume_sma_20']
    
    # Momentum
    features['momentum_10'] = close_series - close_series.shift(10)
    features['momentum_20'] = close_series - close_series.shift(20)
    
    return features.fillna(0)


def create_labels(df: pd.DataFrame, horizon: int = 1, threshold: float = 0.01) -> np.ndarray:
    """創建標籤: 漲>1%=2, 跌<-1%=0, 其他=1"""
    close = df['Close'].values
    future_returns = (close[horizon:] - close[:-horizon]) / close[:-horizon]
    
    labels = np.ones(len(close)) * 1
    for i in range(len(future_returns)):
        if future_returns[i] > threshold:
            labels[i] = 2
        elif future_returns[i] < -threshold:
            labels[i] = 0
    
    return labels[horizon:]


# ============ Data Preparation ============

def prepare_data(symbol: str, period: str = "2y") -> tuple:
    """準備訓練數據"""
    df = yf.download(symbol, period=period, progress=False)
    if df.empty:
        return None, None
    
    # Calculate features
    features = calculate_features(df)
    
    # Create labels (predict next day)
    labels = create_labels(df)
    
    # Align
    min_len = min(len(features), len(labels))
    X = features.values[:min_len]
    y = labels[:min_len]
    
    # Remove NaN
    valid_idx = ~np.isnan(X).any(axis=1)
    X = X[valid_idx]
    y = y[valid_idx]
    
    return X, y


# ============ Model Training ============

def train_models(X: np.ndarray, y: np.ndarray, output_dir: str):
    """訓練多種模型"""
    
    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, shuffle=False
    )
    
    # Scale data
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    results = {}
    
    # 1. Random Forest
    logger.info("Training Random Forest...")
    rf = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42, n_jobs=-1)
    rf.fit(X_train_scaled, y_train)
    rf_pred = rf.predict(X_test_scaled)
    rf_acc = accuracy_score(y_test, rf_pred)
    logger.info(f"  Random Forest Accuracy: {rf_acc:.4f}")
    joblib.dump(rf, f"{output_dir}/model_random_forest.pkl")
    results['random_forest'] = rf_acc
    
    # 2. XGBoost
    logger.info("Training XGBoost...")
    try:
        import xgboost as xgb
        xgb_model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            random_state=42,
            use_label_encoder=False,
            eval_metric='mlogloss'
        )
        xgb_model.fit(X_train_scaled, y_train)
        xgb_pred = xgb_model.predict(X_test_scaled)
        xgb_acc = accuracy_score(y_test, xgb_pred)
        logger.info(f"  XGBoost Accuracy: {xgb_acc}")
        xgb_model.save_model(f"{output_dir}/model_xgboost.json")
        results['xgboost'] = xgb_acc
    except Exception as e:
        logger.error(f"XGBoost error: {e}")
    
    # 3. LightGBM
    logger.info("Training LightGBM...")
    try:
        import lightgbm as lgb
        lgb_model = lgb.LGBMClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            random_state=42,
            verbose=-1
        )
        lgb_model.fit(X_train_scaled, y_train)
        lgb_pred = lgb_model.predict(X_test_scaled)
        lgb_acc = accuracy_score(y_test, lgb_pred)
        logger.info(f"  LightGBM Accuracy: {lgb_acc}")
        lgb_model.booster_.save_model(f"{output_dir}/model_lightgbm.txt")
        results['lightgbm'] = lgb_acc
    except Exception as e:
        logger.error(f"LightGBM error: {e}")
    
    # 4. Logistic Regression
    logger.info("Training Logistic Regression...")
    lr = LogisticRegression(max_iter=1000, random_state=42)
    lr.fit(X_train_scaled, y_train)
    lr_pred = lr.predict(X_test_scaled)
    lr_acc = accuracy_score(y_test, lr_pred)
    logger.info(f"  Logistic Regression Accuracy: {lr_acc}")
    joblib.dump(lr, f"{output_dir}/model_logistic_regression.pkl")
    results['logistic_regression'] = lr_acc
    
    # 5. SVM
    logger.info("Training SVM...")
    svm = SVC(kernel='rbf', C=1.0, random_state=42)
    svm.fit(X_train_scaled, y_train)
    svm_pred = svm.predict(X_test_scaled)
    svm_acc = accuracy_score(y_test, svm_pred)
    logger.info(f"  SVM Accuracy: {svm_acc}")
    joblib.dump(svm, f"{output_dir}/model_svm.pkl")
    results['svm'] = svm_acc
    
    # 6. LSTM
    logger.info("Training LSTM...")
    try:
        # Prepare LSTM data
        X_train_lstm = torch.FloatTensor(X_train_scaled).unsqueeze(1)
        y_train_lstm = torch.LongTensor(y_train.astype(int))
        
        lstm = LSTMModel(input_dim=X_train_scaled.shape[1])
        criterion = nn.CrossEntropyLoss()
        optimizer = torch.optim.Adam(lstm.parameters(), lr=0.001)
        
        for epoch in range(20):
            optimizer.zero_grad()
            outputs = lstm(X_train_lstm)
            loss = criterion(outputs, y_train_lstm)
            loss.backward()
            optimizer.step()
        
        lstm.eval()
        with torch.no_grad():
            lstm_pred = lstm(torch.FloatTensor(X_test_scaled).unsqueeze(1)).argmax(dim=1).numpy()
        lstm_acc = accuracy_score(y_test, lstm_pred)
        logger.info(f"  LSTM Accuracy: {lstm_acc}")
        torch.save(lstm.state_dict(), f"{output_dir}/model_lstm.pth")
        results['lstm'] = lstm_acc
    except Exception as e:
        logger.error(f"LSTM error: {e}")
    
    # Save scaler
    joblib.dump(scaler, f"{output_dir}/scaler.pkl")
    
    return results


# ============ Ensemble Voting ============

def ensemble_predict(X: np.ndarray, output_dir: str) -> dict:
    """集成預測"""
    import xgboost as xgb
    import lightgbm as lgb
    
    # Load scaler
    scaler = joblib.load(f"{output_dir}/scaler.pkl")
    X_scaled = scaler.transform(X)
    
    predictions = {}
    
    # Load models and predict
    try:
        rf = joblib.load(f"{output_dir}/model_random_forest.pkl")
        predictions['random_forest'] = rf.predict(X_scaled)[0]
    except:
        pass
    
    try:
        xgb_model = xgb.XGBClassifier()
        xgb_model.load_model(f"{output_dir}/model_xgboost.json")
        predictions['xgboost'] = xgb_model.predict(X_scaled)[0]
    except:
        pass
    
    try:
        lgb_model = lgb.LGBMClassifier(verbose=-1)
        lgb_model.booster_.load_model(f"{output_dir}/model_lightgbm.txt")
        predictions['lightgbm'] = lgb_model.predict(X_scaled)[0]
    except:
        pass
    
    try:
        lr = joblib.load(f"{output_dir}/model_logistic_regression.pkl")
        predictions['logistic_regression'] = lr.predict(X_scaled)[0]
    except:
        pass
    
    try:
        svm = joblib.load(f"{output_dir}/model_svm.pkl")
        predictions['svm'] = svm.predict(X_scaled)[0]
    except:
        pass
    
    # Voting
    votes = list(predictions.values())
    if votes:
        from collections import Counter
        ensemble_pred = Counter(votes).most_common(1)[0][0]
        return {
            'predictions': predictions,
            'ensemble': int(ensemble_pred),
            'vote_count': len(votes)
        }
    
    return {}


# ============ Main ============

def main():
    parser = argparse.ArgumentParser(description="Multi-Model Training System")
    parser.add_argument("--symbol", default="NVDA", help="Stock symbol")
    parser.add_argument("--period", default="2y", help="Data period")
    parser.add_argument("--output", default="v3_pipeline/models/trained_models", help="Output directory")
    args = parser.parse_args()
    
    logger.info("="*60)
    logger.info("🚀 Kiro Quant V3 - Multi-Model Training System")
    logger.info("="*60)
    logger.info(f"Symbol: {args.symbol}")
    logger.info(f"Period: {args.period}")
    
    # Prepare data
    logger.info("\n[1/3] Preparing data...")
    X, y = prepare_data(args.symbol, args.period)
    
    if X is None:
        logger.error("No data prepared!")
        return
    
    logger.info(f"  Data shape: {X.shape}")
    logger.info(f"  Label distribution: {np.bincount(y.astype(int))}")
    
    # Train models
    logger.info("\n[2/3] Training models...")
    output_dir = f"{args.output}/{args.symbol.lower()}_multi"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    results = train_models(X, y, output_dir)
    
    # Save results
    results_file = f"v3_pipeline/reports/multi_model_results_{args.symbol}_{datetime.now().strftime('%Y%m%d')}.json"
    Path(results_file).parent.mkdir(parents=True, exist_ok=True)
    
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    # Print summary
    logger.info("\n" + "="*60)
    logger.info("📊 模型準確率總結")
    logger.info("="*60)
    
    sorted_results = sorted(results.items(), key=lambda x: x[1], reverse=True)
    for i, (model, acc) in enumerate(sorted_results, 1):
        logger.info(f"  {i}. {model}: {acc:.4f}")
    
    # Save to reports
    with open(results_file, 'w') as f:
        json.dump({
            'models': results,
            'best_model': sorted_results[0][0] if sorted_results else None,
            'best_accuracy': sorted_results[0][1] if sorted_results else None
        }, f, indent=2)
    
    logger.info(f"\n✅ Models saved to: {output_dir}")
    logger.info(f"✅ Results saved to: {results_file}")


if __name__ == "__main__":
    main()
