import pandas as pd
import numpy as np
from pathlib import Path
import datetime
from sklearn.preprocessing import StandardScaler

def compute_indicators(df):
    # 確保數據只有單層 index (yfinance 有時會出 multi-index)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # 1. 趨勢指標: 簡單移動平均線 (SMA)
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['SMA_50'] = df['Close'].rolling(window=50).mean()
    df['SMA_200'] = df['Close'].rolling(window=200).mean()
    
    # 價格與均線的距離 (百分比)
    df['Dist_SMA_20'] = (df['Close'] - df['SMA_20']) / df['SMA_20']
    df['Dist_SMA_50'] = (df['Close'] - df['SMA_50']) / df['SMA_50']
    df['Dist_SMA_200'] = (df['Close'] - df['SMA_200']) / df['SMA_200']

    # 2. 動量指標: RSI (14日)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    # 防止除以 0 產生 inf
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = 100 - (100 / (1 + rs))

    # 3. 波動指標: 價格波動率 (20日標準差)
    df['Volatility'] = df['Close'].pct_change().rolling(window=20).std()

    # 4. 量能指標: 成交量變化率
    df['Vol_Change'] = df['Volume'].pct_change(periods=5)

    # 5. 收益率
    df['Daily_Return'] = df['Close'].pct_change()

    # --- 重要修復：處理無限大 (inf) 與 空白 (NaN) ---
    # 將所有 inf 變成 NaN
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    # 刪除所有含有 NaN 的行
    df.dropna(inplace=True)
    
    return df

def prepare_training_set():
    data_dir = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/data/hsi_training")
    output_file = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/data/hsi_training_processed.parquet")
    
    all_sequences = []
    
    files = list(data_dir.glob("*.parquet"))
    print(f"🚀 Processing {len(files)} stocks...")

    for file in files:
        try:
            df = pd.read_parquet(file)
            if len(df) < 250: continue
            
            # 計算指標
            df = compute_indicators(df)
            
            if df.empty:
                continue

            # 定義標籤: 預測未來 5 個交易日是否上漲超過 1%
            # Label = 1 if Price(t+5) > Price(t) * 1.01 else 0
            # 注意：因為要用到 shift(-5)，所以計算完標籤後可能會有新的 NaN，要再 drop 一次
            df['Target'] = (df['Close'].shift(-5) > df['Close'] * 1.01).astype(int)
            df.dropna(subset=['Target'], inplace=True)
            
            # 我們只需要特徵列
            features = ['Dist_SMA_20', 'Dist_SMA_50', 'Dist_SMA_200', 'RSI', 'Volatility', 'Vol_Change', 'Daily_Return']
            
            # 確保特徵列存在
            existing_features = [f for f in features if f in df.columns]
            
            # 儲存數據
            stock_data = df[existing_features + ['Target']]
            all_sequences.append(stock_data)
            
        except Exception as e:
            print(f"❌ Error processing {file.name}: {e}")

    if not all_sequences:
        print("No data processed. Check if any stocks have sufficient data after cleaning.")
        return

    # 合併所有股票數據
    full_df = pd.concat(all_sequences)
    
    # 再次檢查，確保最後餵俾 scaler 嘅數據係乾淨嘅
    full_df.replace([np.inf, -np.inf], np.nan, inplace=True)
    full_df.dropna(inplace=True)

    # 歸一化 (Normalization)
    scaler = StandardScaler()
    feature_cols = [f for f in ['Dist_SMA_20', 'Dist_SMA_50', 'Dist_SMA_200', 'RSI', 'Volatility', 'Vol_Change', 'Daily_Return'] if f in full_df.columns]
    
    full_df[feature_cols] = scaler.fit_transform(full_df[feature_cols])
    
    # 保存最終訓練集
    full_df.to_parquet(output_file)
    print(f"\n🎉 Preprocessing Finished!")
    print(f"Total samples: {len(full_df)}")
    print(f"Saved to: {output_file}")

if __name__ == '__main__':
    prepare_training_set()
