import torch
import pandas as pd
import numpy as np
from v3_pipeline.models.manager import ModelManager, DataPreparer, AttentiveKiroLSTM
from v3_pipeline.models.brain import KiroLSTM

def verify_fix():
    print("Starting ModelManager scaling validation (Refined)...")
    
    # 1. Setup model manager and load real HK model
    preparer = DataPreparer(lookback=60, target_col="Close")
    model = KiroLSTM(input_dim=24)
    abs_model_dir = "/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/v3_pipeline/models/trained_models"
    manager = ModelManager(model=model, data_preparer=preparer, model_dir=abs_model_dir)
    
    try:
        manager.load("v3_hk_stocks")
        real_features = manager.data_preparer.feature_columns
        print(f"Loaded v3_hk_stocks. Real Features: {real_features[:5]}... Total: {len(real_features)}")
    except Exception as e:
        print(f"Failed to load real model: {e}")
        return

    # 2. Create dummy data using REAL feature names
    dates = pd.date_range(start="2026-04-01", periods=100, freq='min')
    data = {
        "Date": dates,
        "Open": 8.07, "High": 8.10, "Low": 8.05, "Close": 8.07, "Volume": 1000,
        "Dividends": 0.0, "Stock Splits": 0.0
    }
    # Add actual feature columns with dummy values
    for feat in real_features:
        if feat not in data:
            data[feat] = 0.5
            
    df = pd.DataFrame(data)
        
    # 3. Fit a LOCAL preparer for this symbol with the correct columns
    symbol_preparer = DataPreparer(lookback=60, target_col="Close")
    symbol_preparer.fit_transform(df)
    print(f"Local preparer fitted. Local Target min={symbol_preparer.target_min}, max={symbol_preparer.target_max}")

    # 4. Predict
    try:
        # Pass the local preparer! This should trigger the fix
        prediction = manager.predict(df.tail(70), data_preparer=symbol_preparer)
        print(f"\nRESULT: Prediction = {prediction:.6f}")
        
        # Validation logic: 8.07 price should predict something near 8.07 (within 10-20% max)
        if 6.0 < prediction < 10.0:
            print("✨ SUCCESS: Scaling fix confirmed. Prediction is realistic for $8.07 stock.")
        else:
            print("❌ FAILURE: Prediction is still skewed (probably using global scale).")
            
    except Exception as e:
        print(f"Prediction failed: {e}")

if __name__ == "__main__":
    verify_fix()
