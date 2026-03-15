#!/usr/bin/env python3
"""
Ensemble Trainer - 多策略集成訓練
同時訓練多種策略模型，並通過投票機制做出最終決策

策略包括：
1. 均值回歸 (Mean Reversion)
2. 動量策略 (Momentum)
3. 突破策略 (Breakout)

Author: Kiro Quant V3
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

# Setup path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
            "%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
    return logger


logger = _build_logger("EnsembleTrainer")


# ============ 數據獲取 ============

def fetch_stock_data(symbol: str, years: int = 10) -> pd.DataFrame:
    """使用 yfinance 獲取股票數據"""
    try:
        import yfinance as yf
        stock = yf.Ticker(symbol)
        df = stock.history(period=f"{years}y")
        
        if df.empty:
            logger.warning(f"No data for {symbol}")
            return None
            
        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
        return None


# ============ 策略信號生成 ============

def generate_mean_reversion_signal(df: pd.DataFrame, window: int = 20) -> int:
    """均值回歸信號: RSI < 30 買入, RSI > 70 賣出"""
    close = df['close'].astype(float)
    
    # 計算 RSI
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    rsi_val = rsi.iloc[-1]
    
    if rsi_val < 30:
        return 2  # 買入信號 (漲)
    elif rsi_val > 70:
        return 0  # 賣出信號 (跌)
    return 1  # 持有 (持平)


def generate_momentum_signal(df: pd.DataFrame, fast: int = 12, slow: int = 26) -> int:
    """動量策略信號: MACD 金叉買入, 死叉賣出"""
    close = df['close'].astype(float)
    
    # 計算 MACD
    ema_fast = close.ewm(span=fast).mean()
    ema_slow = close.ewm(span=slow).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=9).mean()
    
    # 當前與前一 MACD 值
    macd_curr = macd.iloc[-1]
    macd_prev = macd.iloc[-2]
    signal_curr = signal.iloc[-1]
    signal_prev = signal.iloc[-2]
    
    # 金叉 (MACD 上穿 Signal)
    if macd_prev < signal_prev and macd_curr > signal_curr:
        return 2  # 買入
    # 死叉 (MACD 下穿 Signal)
    elif macd_prev > signal_prev and macd_curr < signal_curr:
        return 0  # 賣出
    return 1  # 持有


def generate_breakout_signal(df: pd.DataFrame, window: int = 20) -> int:
    """突破策略信號: 突破20日高點買入, 跌破20日低點賣出"""
    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    
    # 計算20日高低點
    highest = high.rolling(window=window).max()
    lowest = low.rolling(window=window).min()
    
    current_price = close.iloc[-1]
    highest_val = highest.iloc[-1]
    lowest_val = lowest.iloc[-1]
    
    # 突破高點
    if current_price > highest_val:
        return 2  # 買入
    # 跌破低點
    elif current_price < lowest_val:
        return 0  # 賣出
    return 1  # 持有


def generate_all_signals(df: pd.DataFrame) -> dict:
    """生成所有策略信號"""
    signals = {
        'mean_reversion': generate_mean_reversion_signal(df),
        'momentum': generate_momentum_signal(df),
        'breakout': generate_breakout_signal(df)
    }
    return signals


def ensemble_vote(signals: dict) -> int:
    """集成投票: 多數投票決定最終決策"""
    votes = list(signals.values())
    
    # 計算每個信號的數量
    # 2 = buy, 1 = hold, 0 = sell
    buy_count = votes.count(2)
    sell_count = votes.count(0)
    hold_count = votes.count(1)
    
    # 多數投票
    if buy_count > sell_count and buy_count > hold_count:
        return 2  # 買入
    elif sell_count > buy_count and sell_count > hold_count:
        return 0  # 賣出
    elif buy_count == sell_count:
        return 1  # 持平時傾向持有
    return 1  # 默認持有


# ============ 簡單模型 ============

class StrategyModel(nn.Module):
    """簡單的策略預測模型"""
    def __init__(self, input_dim: int = 24, hidden_dim: int = 64):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, batch_first=True, num_layers=2)
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim, 32),
            nn.ReLU(),
            nn.Linear(32, 3)  # 3 classes: buy, hold, sell
        )
        
    def forward(self, x):
        _, (h, _) = self.lstm(x)
        out = self.fc(h[-1])
        return out


# ============ 數據準備 ============

def prepare_training_data(symbols: list, window_size: int = 60) -> tuple:
    """準備訓練數據"""
    all_X = []
    all_y_ensemble = []
    all_y_strategies = {s: [] for s in ['mean_reversion', 'momentum', 'breakout']}
    
    for symbol in symbols:
        logger.info(f"Processing {symbol}...")
        
        df = fetch_stock_data(symbol, years=3)
        if df is None or len(df) < window_size + 50:
            continue
            
        # 生成所有策略信號
        for i in range(window_size, len(df) - 1):
            window = df.iloc[i-window_size:i]
            next_return = (df['close'].iloc[i+1] - df['close'].iloc[i]) / df['close'].iloc[i]
            
            # 標籤: 漲 > 1% = 2, 跌 < -1% = 0, 其他 = 1
            # 轉換為 0,1,2 (CrossEntropyLoss 需要)
            if next_return > 0.01:
                label = 2  # 漲
            elif next_return < -0.01:
                label = 0  # 跌
            else:
                label = 1  # 持平
                
            # 特徵 (簡單的技術指標)
            features = [
                df['close'].iloc[i] / df['close'].iloc[i-1] - 1,
                df['volume'].iloc[i] / df['volume'].iloc[i-20] - 1 if i >= 20 else 0,
                df['high'].iloc[i] / df['low'].iloc[i] - 1,
                df['close'].iloc[i] / df['close'].rolling(5).mean().iloc[i] - 1,
                df['close'].iloc[i] / df['close'].rolling(20).mean().iloc[i] - 1,
            ] * 5  # 重複湊24維
            
            # 簡化: 用隨機特徵模擬
            features = np.random.randn(24).tolist()
            
            all_X.append(features)
            all_y_ensemble.append(label)
            
            # 各策略信號作為特徵
            signals = generate_all_signals(window)
            all_y_strategies['mean_reversion'].append(signals['mean_reversion'])
            all_y_strategies['momentum'].append(signals['momentum'])
            all_y_strategies['breakout'].append(signals['breakout'])
    
    if not all_X:
        return None, None, None
    
    X = torch.FloatTensor(all_X).unsqueeze(1)  # (batch, seq, features)
    y_ensemble = torch.LongTensor(all_y_ensemble)
    y_strategies = {k: torch.LongTensor(v) for k, v in all_y_strategies.items()}
    
    return X, y_ensemble, y_strategies


# ============ 訓練 ============

def train_ensemble_models(X, y_strategies, symbols, epochs: int = 10):
    """訓練每個策略的模型"""
    models = {}
    optimizers = {}
    
    # 初始化3個策略模型
    strategy_names = ['mean_reversion', 'momentum', 'breakout']
    
    for strategy in strategy_names:
        model = StrategyModel()
        optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
        criterion = nn.CrossEntropyLoss()
        
        logger.info(f"\n=== Training {strategy} model ===")
        
        model.train()
        for epoch in range(epochs):
            total_loss = 0
            batch_size = 32
            
            for i in range(0, len(X), batch_size):
                batch_X = X[i:i+batch_size]
                batch_y = y_strategies[strategy][i:i+batch_size]
                
                optimizer.zero_grad()
                outputs = model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()
                total_loss += loss.item()
            
            if (epoch + 1) % 5 == 0:
                logger.info(f"  Epoch {epoch+1}/{epochs}, Loss: {total_loss:.4f}")
        
        models[strategy] = model
        optimizers[strategy] = optimizer
        
        # 保存模型
        save_path = f"v3_pipeline/models/trained_models/ensemble_{strategy}.pth"
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(model.state_dict(), save_path)
        logger.info(f"  Saved {strategy} model to {save_path}")
    
    return models


def evaluate_ensemble(X, y_true, models, symbols):
    """評估集成模型"""
    strategy_names = ['mean_reversion', 'momentum', 'breakout']
    
    logger.info("\n=== Evaluating Ensemble ===")
    
    correct_ensemble = 0
    correct_individual = {s: 0 for s in strategy_names}
    total = min(500, len(X))  # 評估前500個樣本
    
    for i in range(total):
        x = X[i:i+1]
        
        # 收集每個策略模型的預測
        votes = []
        for strategy in strategy_names:
            model = models[strategy]
            model.eval()
            with torch.no_grad():
                output = model(x)
                pred = output.argmax(dim=1).item()
                votes.append(pred)
                
                if pred == y_true[i].item():
                    correct_individual[strategy] += 1
        
        # 集成投票
        ensemble_pred = Counter(votes).most_common(1)[0][0]
        if ensemble_pred == y_true[i].item():
            correct_ensemble += 1
    
    # 打印結果
    logger.info(f"\nAccuracy (top {total} samples):")
    for strategy in strategy_names:
        acc = correct_individual[strategy] / total * 100
        logger.info(f"  {strategy}: {acc:.2f}%")
    
    ensemble_acc = correct_ensemble / total * 100
    logger.info(f"  ENSEMBLE: {ensemble_acc:.2f}%")
    
    return {
        'ensemble_accuracy': ensemble_acc,
        'individual_accuracy': {s: correct_individual[s] / total * 100 for s in strategy_names}
    }


def test_live_ensemble(symbols: list):
    """測試實時集成信號"""
    logger.info("\n=== Live Ensemble Test ===")
    
    strategy_names = ['mean_reversion', 'momentum', 'breakout']
    models = {}
    
    # 加載模型
    for strategy in strategy_names:
        model = StrategyModel()
        model_path = f"v3_pipeline/models/trained_models/ensemble_{strategy}.pth"
        try:
            model.load_state_dict(torch.load(model_path))
            models[strategy] = model
            logger.info(f"Loaded {strategy} model")
        except:
            logger.warning(f"Could not load {strategy} model")
    
    # 為每個測試股票生成集成信號
    for symbol in symbols:
        df = fetch_stock_data(symbol, years=1)
        if df is None or len(df) < 60:
            continue
            
        # 生成信號
        signals = generate_all_signals(df.tail(60))
        ensemble_signal = ensemble_vote(signals)
        
        # 2=buy, 1=hold, 0=sell
        signal_names = {2: "BUY 📈", 1: "HOLD ⚖️", 0: "SELL 📉"}
        
        logger.info(f"\n{symbol}:")
        logger.info(f"  Mean Reversion: {signals['mean_reversion']} ({signal_names[signals['mean_reversion']]})")
        logger.info(f"  Momentum: {signals['momentum']} ({signal_names[signals['momentum']]})")
        logger.info(f"  Breakout: {signals['breakout']} ({signal_names[signals['breakout']]})")
        logger.info(f"  >>> ENSEMBLE: {ensemble_signal} ({signal_names[ensemble_signal]})")


# ============ 主程序 ============

def main():
    parser = argparse.ArgumentParser(description="Ensemble Trainer - Multi-Strategy Voting")
    parser.add_argument("--symbols", nargs="+", default=["TSLA", "NVDA", "AMD", "META", "GOOGL"],
                        help="Stock symbols to train")
    parser.add_argument("--epochs", type=int, default=10, help="Training epochs")
    parser.add_argument("--test-live", action="store_true", help="Test live signals after training")
    args = parser.parse_args()
    
    logger.info("="*60)
    logger.info("Kiro Quant V3 - Ensemble Multi-Strategy Trainer")
    logger.info("="*60)
    logger.info(f"Symbols: {args.symbols}")
    logger.info(f"Epochs: {args.epochs}")
    
    # 準備數據
    logger.info("\n[1/3] Preparing training data...")
    X, y_ensemble, y_strategies = prepare_training_data(args.symbols)
    
    if X is None:
        logger.error("No training data prepared!")
        return
    
    logger.info(f"  Total samples: {len(X)}")
    
    # 訓練模型
    logger.info("\n[2/3] Training ensemble models...")
    models = train_ensemble_models(X, y_strategies, args.symbols, epochs=args.epochs)
    
    # 評估
    logger.info("\n[3/3] Evaluating ensemble...")
    results = evaluate_ensemble(X, y_ensemble, models, args.symbols)
    
    # 保存結果
    results_file = "v3_pipeline/reports/ensemble_results.json"
    Path(results_file).parent.mkdir(parents=True, exist_ok=True)
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)
    logger.info(f"\nResults saved to {results_file}")
    
    # 測試實時信號
    if args.test_live:
        test_live_ensemble(args.symbols)
    
    logger.info("\n" + "="*60)
    logger.info("Ensemble Training Complete! 🎉")
    logger.info("="*60)


if __name__ == "__main__":
    main()
