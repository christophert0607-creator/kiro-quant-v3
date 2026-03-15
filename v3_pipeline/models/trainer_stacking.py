#!/usr/bin/env python3
"""
Stacking Meta-Learner System
=========================
將多個 base model 既輸出當作 feature，再 train 一層 meta-learner

原理:
Base Models (6個) → 輸出概率 → Meta Features → Meta-Learner → 最終預測

Author: Kiro Quant V3
"""

import argparse
import json
import logging
import sys
import os
from pathlib import Path
from datetime import datetime
from collections import Counter

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
logger = logging.getLogger("StackingMetaLearner")


# ============ Base Models ============

class LSTMModel(nn.Module):
    def __init__(self, input_dim, hidden_dim=64):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, batch_first=True, num_layers=2)
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, 32),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(32, 3)
        )
    
    def forward(self, x):
        _, (h, _) = self.lstm(x)
        return self.fc(h[-1])
    
    def predict_proba(self, x):
        with torch.no_grad():
            out = self.forward(x)
            return torch.softmax(out, dim=1).numpy()


# ============ Feature Engineering ============

def calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    """計算技術指標特徵"""
    close = df['Close'].values.flatten()
    high = df['High'].values.flatten()
    low = df['Low'].values.flatten()
    volume = df['Volume'].values.flatten()
    
    features = pd.DataFrame()
    close_series = pd.Series(close)
    
    # Price features
    for window in [5, 10, 20]:
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
    
    # Momentum
    features['momentum_10'] = close_series - close_series.shift(10)
    
    # Volume
    volume_series = pd.Series(volume)
    features['volume_ratio'] = volume / volume_series.rolling(20).mean()
    
    return features.fillna(0)


def create_labels(df: pd.DataFrame, horizon: int = 1, threshold: float = 0.01) -> np.ndarray:
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

def prepare_data(symbol: str, period: str = "3y") -> tuple:
    """準備訓練數據"""
    df = yf.download(symbol, period=period, progress=False)
    if df.empty:
        return None, None
    
    features = calculate_features(df)
    labels = create_labels(df)
    
    min_len = min(len(features), len(labels))
    X = features.values[:min_len]
    y = labels[:min_len]
    
    valid_idx = ~np.isnan(X).any(axis=1)
    X = X[valid_idx]
    y = y[valid_idx]
    
    return X, y


# ============ Train Base Models ============

def train_base_models(X_train, y_train, X_test, output_dir):
    """訓練所有 base models"""
    
    base_models = {}
    
    # 1. Random Forest
    logger.info("Training Base Model 1: Random Forest...")
    rf = RandomForestClassifier(n_estimators=100, max_depth=8, random_state=42, n_jobs=-1)
    rf.fit(X_train, y_train)
    base_models['rf'] = rf
    
    # 2. XGBoost
    logger.info("Training Base Model 2: XGBoost...")
    try:
        import xgboost as xgb
        xgb_model = xgb.XGBClassifier(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42, use_label_encoder=False, eval_metric='mlogloss')
        xgb_model.fit(X_train, y_train)
        base_models['xgb'] = xgb_model
    except:
        pass
    
    # 3. LightGBM
    logger.info("Training Base Model 3: LightGBM...")
    try:
        import lightgbm as lgb
        lgb_model = lgb.LGBMClassifier(n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42, verbose=-1)
        lgb_model.fit(X_train, y_train)
        base_models['lgb'] = lgb_model
    except:
        pass
    
    # 4. Logistic Regression
    logger.info("Training Base Model 4: Logistic Regression...")
    lr = LogisticRegression(max_iter=1000, random_state=42)
    lr.fit(X_train, y_train)
    base_models['lr'] = lr
    
    # 5. SVM
    logger.info("Training Base Model 5: SVM...")
    svm = SVC(kernel='rbf', C=1.0, random_state=42, probability=True)
    svm.fit(X_train, y_train)
    base_models['svm'] = svm
    
    # 6. Gradient Boosting
    logger.info("Training Base Model 6: Gradient Boosting...")
    gb = GradientBoostingClassifier(n_estimators=100, max_depth=5, random_state=42)
    gb.fit(X_train, y_train)
    base_models['gb'] = gb
    
    # Save all base models
    for name, model in base_models.items():
        joblib.dump(model, f"{output_dir}/base_{name}.pkl")
    
    return base_models


# ============ Stacking: Generate Meta Features ============

def generate_meta_features(X, base_models, scaler):
    """生成 meta features (每個 base model 既輸出概率)"""
    X_scaled = scaler.transform(X)
    
    meta_features = []
    
    for model_name, model in base_models.items():
        try:
            # Get probability predictions
            if hasattr(model, 'predict_proba'):
                probs = model.predict_proba(X_scaled)
            else:
                # For models without predict_proba
                pred = model.predict(X_scaled)
                probs = np.zeros((len(X), 3))
                for i, p in enumerate(pred):
                    probs[i, int(p)] = 1.0
            
            meta_features.append(probs)
        except Exception as e:
            logger.warning(f"Model {model_name} failed: {e}")
    
    # Combine all meta features
    if meta_features:
        meta_X = np.hstack(meta_features)
        return meta_X
    
    return None


# ============ Train Meta-Learner ============

def train_meta_learner(meta_X_train, y_train, meta_X_test, y_test, output_dir):
    """訓練 meta-learner"""
    
    results = {}
    
    # Meta-Learner 1: Logistic Regression
    logger.info("Training Meta-Learner 1: Logistic Regression...")
    meta_lr = LogisticRegression(max_iter=1000, random_state=42)
    meta_lr.fit(meta_X_train, y_train)
    meta_lr_pred = meta_lr.predict(meta_X_test)
    meta_lr_acc = accuracy_score(y_test, meta_lr_pred)
    logger.info(f"  Meta LR Accuracy: {meta_lr_acc:.4f}")
    joblib.dump(meta_lr, f"{output_dir}/meta_lr.pkl")
    results['meta_lr'] = meta_lr_acc
    
    # Meta-Learner 2: Random Forest
    logger.info("Training Meta-Learner 2: Random Forest...")
    meta_rf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
    meta_rf.fit(meta_X_train, y_train)
    meta_rf_pred = meta_rf.predict(meta_X_test)
    meta_rf_acc = accuracy_score(y_test, meta_rf_pred)
    logger.info(f"  Meta RF Accuracy: {meta_rf_acc:.4f}")
    joblib.dump(meta_rf, f"{output_dir}/meta_rf.pkl")
    results['meta_rf'] = meta_rf_acc
    
    # Meta-Learner 3: XGBoost
    logger.info("Training Meta-Learner 3: XGBoost...")
    try:
        import xgboost as xgb
        meta_xgb = xgb.XGBClassifier(n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42, use_label_encoder=False, eval_metric='mlogloss')
        meta_xgb.fit(meta_X_train, y_train)
        meta_xgb_pred = meta_xgb.predict(meta_X_test)
        meta_xgb_acc = accuracy_score(y_test, meta_xgb_pred)
        logger.info(f"  Meta XGB Accuracy: {meta_xgb_acc:.4f}")
        meta_xgb.save_model(f"{output_dir}/meta_xgb.json")
        results['meta_xgb'] = meta_xgb_acc
    except:
        pass
    
    # Choose best meta-learner
    best_name = max(results, key=results.get)
    best_acc = results[best_name]
    
    logger.info(f"\n🏆 Best Meta-Learner: {best_name} with accuracy {best_acc:.4f}")
    
    return results, best_name


# ============ Confidence Threshold ============

def predict_with_confidence(meta_X, meta_learner, confidence_threshold=0.65):
    """帶置信度既預測"""
    probs = meta_learner.predict_proba(meta_X)[0]
    max_prob = np.max(probs)
    prediction = np.argmax(probs)
    
    if max_prob >= confidence_threshold:
        return prediction, max_prob
    else:
        # Low confidence - return hold (1)
        return 1, max_prob


# ============ Evaluation ============

def evaluate_with_trading_metrics(y_true, y_pred, prices):
    """用交易指標評估"""
    capital = 100000
    position = 0
    trades = []
    
    for i in range(len(y_pred)):
        if i >= len(prices) - 1:
            break
            
        price = prices[i]
        
        # Buy signal (2)
        if y_pred[i] == 2 and position == 0:
            position = capital / price
            capital = 0
            trades.append(('BUY', price))
        
        # Sell signal (0)
        elif y_pred[i] == 0 and position > 0:
            capital = position * price
            position = 0
            trades.append(('SELL', price))
    
    # Final value
    if position > 0:
        final_value = position * prices[-1]
    else:
        final_value = capital
    
    total_return = (final_value - 100000) / 100000 * 100
    
    # Win rate
    wins = 0
    losses = 0
    for j in range(1, len(trades)):
        if trades[j][0] == 'SELL' and trades[j-1][0] == 'BUY':
            if trades[j][1] > trades[j-1][1]:
                wins += 1
            else:
                losses += 1
    
    win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
    
    # Max drawdown
    portfolio_values = [100000]
    current = 100000
    for j in range(len(trades)):
        if trades[j][0] == 'BUY':
            current = 100000  # Simplified
        elif trades[j][0] == 'SELL':
            current *= 1.05  # Simplified return
            portfolio_values.append(current)
    
    max_dd = 0
    peak = portfolio_values[0]
    for v in portfolio_values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
    
    return {
        'total_return': total_return,
        'win_rate': win_rate,
        'max_drawdown': max_dd * 100,
        'num_trades': len(trades)
    }


# ============ Main ============

def main():
    parser = argparse.ArgumentParser(description="Stacking Meta-Learner System")
    parser.add_argument("--symbol", default="NVDA", help="Stock symbol")
    parser.add_argument("--period", default="3y", help="Data period")
    parser.add_argument("--confidence", type=float, default=0.65, help="Confidence threshold")
    parser.add_argument("--output", default="v3_pipeline/models/trained_models", help="Output directory")
    args = parser.parse_args()
    
    logger.info("="*60)
    logger.info("🚀 Kiro Quant V3 - Stacking Meta-Learner System")
    logger.info("="*60)
    logger.info(f"Symbol: {args.symbol}")
    logger.info(f"Confidence Threshold: {args.confidence}")
    
    # Prepare data
    logger.info("\n[1/4] Preparing data...")
    X, y = prepare_data(args.symbol, args.period)
    
    if X is None:
        logger.error("No data!")
        return
    
    logger.info(f"  Data shape: {X.shape}")
    
    # Scale
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # Split: 70% train, 30% test
    split_idx = int(len(X_scaled) * 0.7)
    X_train = X_scaled[:split_idx]
    X_test = X_scaled[split_idx:]
    y_train = y[:split_idx]
    y_test = y[split_idx:]
    
    # Create output directory
    output_dir = f"{args.output}/{args.symbol.lower()}_stacking"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Train base models
    logger.info("\n[2/4] Training Base Models...")
    base_models = train_base_models(X_train, y_train, X_test, output_dir)
    logger.info(f"  Trained {len(base_models)} base models")
    
    # Generate meta features
    logger.info("\n[3/4] Generating Meta Features...")
    meta_X_train = generate_meta_features(X_train, base_models, scaler)
    meta_X_test = generate_meta_features(X_test, base_models, scaler)
    logger.info(f"  Meta features shape: {meta_X_train.shape}")
    
    # Train meta-learner
    logger.info("\n[4/4] Training Meta-Learner...")
    meta_results, best_name = train_meta_learner(meta_X_train, y_train, meta_X_test, y_test, output_dir)
    
    # Save scaler
    joblib.dump(scaler, f"{output_dir}/scaler.pkl")
    
    # Compare with simple voting
    logger.info("\n" + "="*60)
    logger.info("📊 Results Comparison")
    logger.info("="*60)
    
    # Simple voting (average of base models)
    all_preds = []
    for name, model in base_models.items():
        pred = model.predict(X_test)
        all_preds.append(pred)
    
    voting_pred = np.array(all_preds).mean(axis=0).round()
    voting_acc = accuracy_score(y_test, voting_pred)
    
    logger.info(f"Simple Voting Accuracy: {voting_acc:.4f}")
    
    for name, acc in meta_results.items():
        improvement = (acc - voting_acc) / voting_acc * 100
        logger.info(f"  {name}: {acc:.4f} ({improvement:+.1f}% vs voting)")
    
    # Trading evaluation (if have prices)
    try:
        df = yf.download(args.symbol, period=args.period, progress=False)
        if not df.empty:
            prices = df['Close'].values[split_idx:split_idx+len(y_test)]
            trading_metrics = evaluate_with_trading_metrics(y_test, meta_X_test, prices)
            
            logger.info(f"\n💰 Trading Metrics (with best meta-learner):")
            logger.info(f"  Total Return: {trading_metrics['total_return']:.2f}%")
            logger.info(f"  Win Rate: {trading_metrics['win_rate']:.1f}%")
            logger.info(f"  Max Drawdown: {trading_metrics['max_drawdown']:.1f}%")
            logger.info(f"  Num Trades: {trading_metrics['num_trades']}")
    except:
        pass
    
    # Save results
    results_file = f"v3_pipeline/reports/stacking_results_{args.symbol}_{datetime.now().strftime('%Y%m%d')}.json"
    with open(results_file, 'w') as f:
        json.dump({
            'base_models': len(base_models),
            'meta_results': meta_results,
            'best_meta_learner': best_name,
            'voting_accuracy': voting_acc,
            'confidence_threshold': args.confidence
        }, f, indent=2)
    
    logger.info(f"\n✅ Results saved to: {results_file}")
    logger.info(f"✅ Models saved to: {output_dir}")


if __name__ == "__main__":
    main()
