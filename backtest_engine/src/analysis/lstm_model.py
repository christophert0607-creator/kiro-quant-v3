"""
LSTM Stock Prediction Model

用 Deep Learning 預測股價走勢
"""

import numpy as np
import pandas as pd
from typing import Tuple, Optional
import warnings
warnings.filterwarnings('ignore')

# Try to import tensorflow
try:
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, Dense, Dropout
    from tensorflow.keras.callbacks import EarlyStopping
    HAS_TF = True
except ImportError:
    HAS_TF = False
    print("TensorFlow not installed")


class LSTMModel:
    """LSTM 股價預測模型"""
    
    def __init__(self, sequence_length: int = 60, 
                 lstm_units: int = 50,
                 dropout: float = 0.2):
        """
        Args:
            sequence_length: 用幾多日既數據黎預測 (default 60日)
            lstm_units: LSTM 層既神經元數量
            dropout: Dropout rate (防止overfit)
        """
        self.sequence_length = sequence_length
        self.lstm_units = lstm_units
        self.dropout = dropout
        self.model = None
        self.scaler = None
        
    def prepare_data(self, df: pd.DataFrame, target_col: str = 'Close',
                   test_size: float = 0.2) -> Tuple:
        """
        準備訓練數據
        
        Args:
            df: 股價數據
            target_col: 目標列 (Close price)
            test_size: 測試集比例
            
        Returns:
            X_train, X_test, y_train, y_test, scaler
        """
        if not HAS_TF:
            raise ImportError("TensorFlow not installed")
            
        from sklearn.preprocessing import MinMaxScaler
        
        # 提取特徵
        data = df[['Open', 'High', 'Low', 'Close', 'Volume']].values
        
        # Normalize
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        scaled_data = self.scaler.fit_transform(data)
        
        # 創建sequences
        X, y = [], []
        for i in range(self.sequence_length, len(scaled_data)):
            X.append(scaled_data[i-self.sequence_length:i])
            # 預測第二日既收盤價變化
            y.append(scaled_data[i, 3])  # Close is index 3
            
        X, y = np.array(X), np.array(y)
        
        # Split
        split = int(len(X) * (1 - test_size))
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]
        
        return X_train, X_test, y_train, y_test
    
    def build(self, input_shape: Tuple):
        """構建 LSTM 模型"""
        if not HAS_TF:
            return
            
        self.model = Sequential([
            LSTM(self.lstm_units, return_sequences=True, 
                 input_shape=input_shape),
            Dropout(self.dropout),
            
            LSTM(self.lstm_units // 2, return_sequences=False),
            Dropout(self.dropout),
            
            Dense(25, activation='relu'),
            Dense(1)  # 輸出: 股價
        ])
        
        self.model.compile(
            optimizer='adam',
            loss='mse',
            metrics=['mae']
        )
        
        return self.model
    
    def train(self, X_train, y_train, 
              epochs: int = 50, 
              batch_size: int = 32,
              validation_split: float = 0.1) -> dict:
        """訓練模型"""
        if not self.model:
            self.build(X_train.shape[1:])
            
        early_stop = EarlyStopping(
            monitor='val_loss',
            patience=10,
            restore_best_weights=True
        )
        
        history = self.model.fit(
            X_train, y_train,
            epochs=epochs,
            batch_size=batch_size,
            validation_split=validation_split,
            callbacks=[early_stop],
            verbose=0
        )
        
        return history.history
    
    def predict(self, X) -> np.ndarray:
        """預測"""
        if not self.model:
            raise ValueError("Model not trained")
        return self.model.predict(X, verbose=0)
    
    def evaluate(self, X_test, y_test) -> dict:
        """評估模型"""
        predictions = self.predict(X_test)
        
        # Calculate metrics
        mse = np.mean((predictions - y_test) ** 2)
        rmse = np.sqrt(mse)
        mae = np.mean(np.abs(predictions - y_test))
        
        # Direction accuracy
        pred_direction = np.diff(predictions.flatten())
        actual_direction = np.diff(y_test)
        direction_acc = np.mean((pred_direction > 0) == (actual_direction > 0))
        
        return {
            'mse': mse,
            'rmse': rmse,
            'mae': mae,
            'direction_accuracy': direction_acc
        }


def create_lstm_prediction_system():
    """創建完整既 LSTM 預測系統"""
    
    # 1. 獲取數據
    import yfinance as yf
    
    print("=== LSTM Stock Prediction ===")
    
    # 2. 準備數據
    ticker = yf.Ticker('NVDA')
    df = ticker.history(start='2020-01-01', end='2025-01-01')
    
    # 3. 建立模型
    model = LSTMModel(
        sequence_length=60,
        lstm_units=64,
        dropout=0.2
    )
    
    # 4. 準備數據
    X_train, X_test, y_train, y_test = model.prepare_data(df)
    
    # 5. 訓練
    print("Training LSTM...")
    history = model.train(X_train, y_train, epochs=30, batch_size=32)
    
    # 6. 評估
    metrics = model.evaluate(X_test, y_test)
    
    print(f"\n=== Results ===")
    print(f"RMSE: {metrics['rmse']:.4f}")
    print(f"MAE: {metrics['mae']:.4f}")
    print(f"Direction Accuracy: {metrics['direction_accuracy']:.2%}")
    
    return model, metrics


# Test
if __name__ == "__main__":
    if HAS_TF:
        create_lstm_prediction_system()
    else:
        print("Install TensorFlow first: pip install tensorflow")
