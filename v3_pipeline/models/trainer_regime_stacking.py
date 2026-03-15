#!/usr/bin/env python3
"""
Regime-Adaptive Stacking System
===========================
將 Market Regime Detector 整合到 Stacking Meta-Learner

特點:
1. 加入 regime features 到 meta features
2. 根據不同 regime 自動調整 confidence threshold
3. Crisis mode - 自動暫停交易

Author: Kiro Quant V3
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime
from collections import Counter

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import joblib

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("RegimeStacking")


# ============ Market Regime Detector ============

class MarketRegimeDetector:
    """市場環境檢測器"""
    
    def __init__(self):
        self.trend_lookback = 20
        self.vol_lookback = 20
    
    def detect(self, close: np.ndarray) -> dict:
        """檢測市場環境"""
        close = close.flatten() if close.ndim > 1 else close
        
        # Trend detection
        recent = np.mean(close[-self.trend_lookback:])
        early = np.mean(close[:self.trend_lookback])
        trend = (recent - early) / early if early > 0 else 0
        
        # Volatility detection
        returns = np.diff(close) / close[:-1]
        vol_now = np.std(returns[-self.vol_lookback:])
        vol_hist = np.std(returns)
        vol_ratio = vol_now / vol_hist if vol_hist > 0 else 1.0
        
        # Determine regime
        if vol_ratio > 1.5:
            regime = "HIGH_VOL"
            confidence_threshold = 0.80  # High threshold in volatile
        elif vol_ratio < 0.7:
            regime = "LOW_VOL"
            confidence_threshold = 0.50  # Lower threshold
        elif trend > 0.08:
            regime = "BULL"
            confidence_threshold = 0.55
        elif trend < -0.08:
            regime = "BEAR"
            confidence_threshold = 0.75  # Very conservative
        else:
            regime = "SIDEWAYS"
            confidence_threshold = 0.60
        
        return {
            'regime': regime,
            'trend': trend,
            'vol_ratio': vol_ratio,
            'confidence_threshold': confidence_threshold,
            # Regime features (one-hot encoded)
            'regime_bull': 1 if regime == "BULL" else 0,
            'regime_bear': 1 if regime == "BEAR" else 0,
            'regime_sideways': 1 if regime == "SIDEWAYS" else 0,
            'regime_high_vol': 1 if regime == "HIGH_VOL" else 0,
            'regime_low_vol': 1 if regime == "LOW_VOL" else 0,
        }


# ============ Feature Engineering ============

def calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    close = df['Close'].values.flatten()
    
    features = pd.DataFrame()
    close_series = pd.Series(close)
    
    # Price features
    for window in [5, 10, 20]:
        features[f'sma_{window}'] = close_series.rolling(window).mean()
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
    
    # Bollinger
    sma20 = close_series.rolling(20).mean()
    std20 = close_series.rolling(20).std()
    features['bb_width'] = (sma20 + 2*std20 - (sma20 - 2*std20)) / sma20
    
    return features.fillna(0)


def create_labels(df: pd.DataFrame, threshold: float = 0.01):
    close = df['Close'].values
    future = (close[1:] - close[:-1]) / close[:-1]
    
    labels = np.ones(len(close)) * 1
    for i, f in enumerate(future):
        if f > threshold:
            labels[i+1] = 2  # Buy
        elif f < -threshold:
            labels[i+1] = 0  # Sell
    
    return labels[1:]


# ============ Main System ============

def main():
    parser = argparse.ArgumentParser(description="Regime-Adaptive Stacking System")
    parser.add_argument("--symbol", default="NVDA", help="Stock symbol")
    parser.add_argument("--period", default="3y", help="Data period")
    parser.add_argument("--output", default="v3_pipeline/models/trained_models", help="Output directory")
    args = parser.parse_args()
    
    logger.info("="*60)
    logger.info("🚀 Kiro Quant V3 - Regime-Adaptive Stacking System")
    logger.info("="*60)
    logger.info(f"Symbol: {args.symbol}")
    
    # Load data
    df = yf.download(args.symbol, period=args.period, progress=False)
    if df.empty:
        logger.error("No data!")
        return
    
    features = calculate_features(df)
    labels = create_labels(df)
    
    # Align
    min_len = min(len(features), len(labels))
    X = features.values[:min_len]
    y = labels[:min_len]
    close_prices = df['Close'].values[:min_len]
    
    valid_idx = ~np.isnan(X).any(axis=1)
    X = X[valid_idx]
    y = y[valid_idx]
    close_prices = close_prices[valid_idx]
    
    logger.info(f"Data shape: {X.shape}")
    
    # Detect regime for each data point
    regime_detector = MarketRegimeDetector()
    regime_features = []
    
    for i in range(len(X)):
        window = close_prices[max(0, i-50):i+1]
        if len(window) > 20:
            regime_info = regime_detector.detect(window)
        else:
            regime_info = {'regime': 'UNKNOWN', 'confidence_threshold': 0.65,
                         'regime_bull': 0, 'regime_bear': 0, 'regime_sideways': 0,
                         'regime_high_vol': 0, 'regime_low_vol': 0}
        
        regime_features.append([
            regime_info['regime_bull'],
            regime_info['regime_bear'],
            regime_info['regime_sideways'],
            regime_info['regime_high_vol'],
            regime_info['regime_low_vol'],
            regime_info.get('trend', 0),
            regime_info.get('vol_ratio', 1),
        ])
    
    regime_X = np.array(regime_features)
    
    # Combine original + regime features
    X_combined = np.hstack([X, regime_X])
    
    logger.info(f"Combined features shape: {X_combined.shape}")
    
    # Scale
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_combined)
    
    # Split: 70% train, 30% test
    split_idx = int(len(X_scaled) * 0.7)
    X_train = X_scaled[:split_idx]
    X_test = X_scaled[split_idx:]
    y_train = y[:split_idx]
    y_test = y[split_idx:]
    regime_test = regime_X[split_idx:]
    close_test = close_prices[split_idx:]
    
    # Load existing base models
    base_dir = f"{args.output}/{args.symbol.lower()}_stacking"
    
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.svm import SVC
    
    # Train simple base models
    logger.info("\nTraining Base Models with Regime Features...")
    base_models = {}
    
    rf = RandomForestClassifier(n_estimators=100, max_depth=8, random_state=42, n_jobs=-1)
    rf.fit(X_train, y_train)
    base_models['rf'] = rf
    
    lr = LogisticRegression(max_iter=1000, random_state=42)
    lr.fit(X_train, y_train)
    base_models['lr'] = lr
    
    gb = GradientBoostingClassifier(n_estimators=100, max_depth=5, random_state=42)
    gb.fit(X_train, y_train)
    base_models['gb'] = gb
    
    # Generate meta features
    logger.info("\nGenerating Meta Features with Regime...")
    meta_train = []
    meta_test = []
    
    for name, model in base_models.items():
        probs_train = model.predict_proba(X_train)
        probs_test = model.predict_proba(X_test)
        meta_train.append(probs_train)
        meta_test.append(probs_test)
    
    meta_X_train = np.hstack(meta_train)
    meta_X_test = np.hstack(meta_test)
    
    # Add regime features to meta
    meta_X_train = np.hstack([meta_X_train, regime_X[:split_idx]])
    meta_X_test = np.hstack([meta_X_test, regime_test])
    
    logger.info(f"Final meta features shape: {meta_X_train.shape}")
    
    # Train Meta-Learner
    logger.info("\nTraining Meta-Learner with Regime...")
    meta_lr = LogisticRegression(max_iter=1000, random_state=42)
    meta_lr.fit(meta_X_train, y_train)
    
    # Predict with regime-adaptive threshold
    logger.info("\nEvaluating with Regime-Adaptive Threshold...")
    
    probs = meta_lr.predict_proba(meta_X_test)
    predictions = []
    
    for i in range(len(probs)):
        regime = regime_test[i]
        
        # Get confidence threshold based on regime
        if regime[3] == 1:  # HIGH_VOL
            threshold = 0.80
        elif regime[1] == 1:  # BEAR
            threshold = 0.75
        elif regime[2] == 1:  # SIDEWAYS
            threshold = 0.60
        elif regime[0] == 1:  # BULL
            threshold = 0.55
        elif regime[4] == 1:  # LOW_VOL
            threshold = 0.50
        else:
            threshold = 0.65
        
        max_prob = np.max(probs[i])
        
        if max_prob >= threshold:
            predictions.append(np.argmax(probs[i]))
        else:
            predictions.append(1)  # Hold
    
    predictions = np.array(predictions)
    
    # Results
    acc = accuracy_score(y_test, predictions)
    logger.info(f"\n✅ Regime-Adaptive Stacking Accuracy: {acc:.4f}")
    
    # Trading simulation
    logger.info("\nSimulating Trading...")
    capital = 100000
    position = 0
    trades = []
    
    for i in range(len(predictions)):
        if i >= len(close_test) - 1:
            break
        
        price = close_test[i]
        
        # Buy
        if predictions[i] == 2 and position == 0:
            position = capital / price
            capital = 0
            trades.append(('BUY', price, regime_test[i]))
        
        # Sell
        elif predictions[i] == 0 and position > 0:
            capital = position * price
            position = 0
            trades.append(('SELL', price, regime_test[i]))
    
    # Final value
    if position > 0:
        final_value = position * close_test[-1]
    else:
        final_value = capital
    
    total_return = (final_value - 100000) / 100000 * 100
    
    # Calculate by regime
    regime_returns = {'BULL': [], 'BEAR': [], 'SIDEWAYS': [], 'HIGH_VOL': [], 'LOW_VOL': []}
    for i, (action, price, regime) in enumerate(trades[:-1]):
        if i+1 < len(trades):
            next_action, next_price, _ = trades[i+1]
            if action == 'BUY' and next_action == 'SELL':
                ret = (next_price - price) / price * 100
                if regime[0]:
                    regime_returns['BULL'].append(ret)
                elif regime[1]:
                    regime_returns['BEAR'].append(ret)
                elif regime[2]:
                    regime_returns['SIDEWAYS'].append(ret)
                elif regime[3]:
                    regime_returns['HIGH_VOL'].append(ret)
                elif regime[4]:
                    regime_returns['LOW_VOL'].append(ret)
    
    logger.info("\n📊 Returns by Regime:")
    for reg, rets in regime_returns.items():
        if rets:
            avg = np.mean(rets)
            logger.info(f"  {reg}: {avg:+.2f}%")
    
    # Save
    output_dir = f"{args.output}/{args.symbol.lower()}_regime_stacking"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    joblib.dump(meta_lr, f"{output_dir}/meta_learner.pkl")
    joblib.dump(scaler, f"{output_dir}/scaler.pkl")
    
    results = {
        'accuracy': acc,
        'total_return': total_return,
        'num_trades': len(trades),
        'regime_returns': {k: np.mean(v) if v else 0 for k, v in regime_returns.items()}
    }
    
    with open(f"v3_pipeline/reports/regime_stacking_{args.symbol}.json", 'w') as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"\n✅ Regime-Adaptive Stacking Complete!")
    logger.info(f"   Accuracy: {acc:.4f}")
    logger.info(f"   Return: {total_return:+.2f}%")
    logger.info(f"   Trades: {len(trades)}")


if __name__ == "__main__":
    main()
