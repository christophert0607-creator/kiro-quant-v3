#!/usr/bin/env python3
"""訓練所有股票的模型"""

import os
import sys
sys.path.insert(0, '.')

from pathlib import Path
import json
import pandas as pd
import numpy as np
from datetime import datetime
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

# 加載配置
with open('config.json') as f:
    config = json.load(f)

stocks = config['v3_live']['symbols_list']
stocks_dir = Path('stocks')
models_dir = Path('models')
models_dir.mkdir(exist_ok=True)

print(f"開始訓練 {len(stocks)} 隻股票的模型...")

for symbol in stocks:
    print(f"\n=== 訓練 {symbol} ===")
    
    # 加載數據
    data_path = stocks_dir / f"{symbol}.csv"
    if not data_path.exists():
        print(f"  ❌ 無數據: {symbol}")
        continue
    
    df = pd.read_csv(data_path, parse_dates=['Date'], index_col='Date')
    
    # 計算技術指標
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA10'] = df['Close'].rolling(10).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['RSI'] = 100 - (100 / (1 + df['Close'].pct_change().rolling(14).apply(lambda x: x[x>0].sum() / (-x[x<0].sum()) if len(x[x<0]) > 0 else 0)))
    df['Returns'] = df['Close'].pct_change()
    df = df.dropna()
    
    # 準備特徵
    features = ['Open', 'High', 'Low', 'Close', 'Volume', 'MA5', 'MA10', 'MA20', 'RSI', 'Returns']
    X = df[features].values
    y = (df['Close'].shift(-1) > df['Close']).astype(int).values
    
    # 去掉最後一行（沒有標籤）
    X = X[:-1]
    y = y[:-1]
    
    # 標準化
    X_mean = X.mean(axis=0)
    X_std = X.std(axis=0) + 1e-8
    X = (X - X_mean) / X_std
    
    # 簡單模型
    class SimpleModel(nn.Module):
        def __init__(self, input_dim):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(input_dim, 64),
                nn.ReLU(),
                nn.Dropout(0.2),
                nn.Linear(64, 32),
                nn.ReLU(),
                nn.Linear(32, 2)
            )
        def forward(self, x):
            return self.net(x)
    
    # 訓練
    X_tensor = torch.FloatTensor(X)
    y_tensor = torch.LongTensor(y)
    
    model = SimpleModel(X.shape[1])
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    
    for epoch in range(20):
        model.train()
        optimizer.zero_grad()
        outputs = model(X_tensor)
        loss = criterion(outputs, y_tensor)
        loss.backward()
        optimizer.step()
        
        if epoch % 5 == 0:
            print(f"  Epoch {epoch}: loss={loss.item():.4f}")
    
    # 保存模型
    model_path = models_dir / f"{symbol}_model.pt"
    torch.save({
        'model': model.state_dict(),
        'mean': X_mean,
        'std': X_std
    }, model_path)
    
    print(f"  ✅ 已保存: {model_path}")

print("\n=== 訓練完成 ===")
