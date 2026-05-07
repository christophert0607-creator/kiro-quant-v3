import pandas as pd
import numpy as np
from pathlib import Path
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

# 1. LSTM Model Definition
class HSILSTM(nn.Module):
    def __init__(self, input_dim, hidden_dim=64, num_layers=2):
        super(HSILSTM, self).__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True, dropout=0.2)
        self.fc = nn.Linear(hidden_dim, 1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        # x shape: (batch, seq_len, input_dim)
        out, _ = self.lstm(x)
        # Take the last time step output
        out = self.fc(out[:, -1, :])
        return self.sigmoid(out)

def create_sequences(data, target, seq_length=30):
    X, y = [], []
    for i in range(len(data) - seq_length):
        X.append(data[i:(i + seq_length)])
        y.append(target[i + seq_length])
    return np.array(X), np.array(y)

def train_model():
    # Path settings
    processed_file = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/data/hsi_training_processed.parquet")
    model_path = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/models/hsi_lstm_v1.pth")
    model_path.parent.mkdir(parents=True, exist_ok=True)

    print("🚀 Loading processed data...")
    df = pd.read_parquet(processed_file)
    
    # Separate features and target
    feature_cols = [c for c in df.columns if c != 'Target']
    X_raw = df[feature_cols].values
    y_raw = df['Target'].values

    # Create sequences (30 days lookback)
    seq_length = 30
    X_seq, y_seq = create_sequences(X_raw, y_raw, seq_length)
    
    print(f"Dataset created: {X_seq.shape[0]} sequences")

    # Split into Train/Test (80/20)
    X_train, X_test, y_train, y_test = train_test_split(X_seq, y_seq, test_size=0.2, shuffle=False)

    # Convert to Tensors
    X_train_t = torch.FloatTensor(X_train)
    y_train_t = torch.FloatTensor(y_train).view(-1, 1)
    X_test_t = torch.FloatTensor(X_test)
    y_test_t = torch.FloatTensor(y_test).view(-1, 1)

    train_loader = DataLoader(TensorDataset(X_train_t, y_train_t), batch_size=64, shuffle=True)

    # Initialize Model
    input_dim = X_raw.shape[1]
    model = HSILSTM(input_dim)
    criterion = nn.BCELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

    # Training Loop
    epochs = 20
    print(f"Starting training for {epochs} epochs...")
    
    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for batch_X, batch_y in train_loader:
            optimizer.zero_grad()
            outputs = model(batch_X)
            loss = criterion(outputs, batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        
        # Evaluation
        model.eval()
        with torch.no_grad():
            test_preds = model(X_test_t)
            test_labels = (test_preds > 0.5).float()
            accuracy = (test_labels == y_test_t).float().mean()
        
        print(f"Epoch [{epoch+1}/{epochs}] - Loss: {total_loss/len(train_loader):.4f} - Test Acc: {accuracy:.4f}")

    # Save Model
    torch.save(model.state_dict(), model_path)
    print(f"\n🎉 Training complete! Model saved to: {model_path}")

if __name__ == '__main__':
    train_model()
