# Kiro Quant V3.0 Pipeline Structure

## 📂 Directory Layout
- **core/**: `trading_engine.py` (Main loop, Futu connectivity)
- **data/**: `data_acquisition.py` (yfinance, massive), `preprocessing.py` (cleaning, scaling)
- **features/**: `feature_generator.py` (TA-Lib, Sentiment analysis)
- **models/**: `brain.py` (Model architecture & prediction logic), `trained_models/`
- **backtest/**: `backtest_engine.py` (Backtrader integration)
- **risk/**: `guard_rail.py` (Monitoring, exit logic)
- **logs/**: Trading and system logs

## 🔄 Current Pipeline Logic
1. **Fetch** -> 2. **Preprocess** -> 3. **Extract Features** -> 4. **Predict** -> 5. **Simulate (if pre-trade)** -> 6. **Execute** -> 7. **Monitor/Report**
