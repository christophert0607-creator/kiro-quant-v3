import torch
from torch import nn


class KiroLSTM(nn.Module):
    """Configurable LSTM network for next-step price/trend prediction."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 64,
        num_layers: int = 2,
        dropout: float = 0.2,
        output_dim: int = 1,
    ) -> None:
        super().__init__()
        effective_dropout = dropout if num_layers > 1 else 0.0

        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=effective_dropout,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_dim, output_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: [batch_size, sequence_length, input_dim]
        lstm_out, _ = self.lstm(x)
        last_step = lstm_out[:, -1, :]
        dropped = self.dropout(last_step)
        output = self.fc(dropped)
        return output


class StockPatternModel(nn.Module):
    """CNN-LSTM multi-task model for price regression + pattern classification."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 128,
        num_layers: int = 2,
        num_patterns: int = 8,
        dropout: float = 0.3,
    ) -> None:
        super().__init__()
        self.temporal_conv = nn.Sequential(
            nn.Conv1d(input_dim, hidden_dim, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(hidden_dim, hidden_dim, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.lstm = nn.LSTM(
            input_size=hidden_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.attn = nn.MultiheadAttention(hidden_dim, num_heads=4, batch_first=True)
        self.norm = nn.LayerNorm(hidden_dim)
        self.dropout = nn.Dropout(dropout)

        self.price_head = nn.Linear(hidden_dim, 1)
        self.pattern_head = nn.Linear(hidden_dim, num_patterns)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        # x: [B, T, C] -> conv expects [B, C, T]
        conv = self.temporal_conv(x.transpose(1, 2)).transpose(1, 2)
        seq, _ = self.lstm(conv)
        attn_out, _ = self.attn(seq, seq, seq, need_weights=False)
        fused = self.norm(seq + attn_out)
        last = self.dropout(fused[:, -1, :])
        return self.price_head(last), self.pattern_head(last)
