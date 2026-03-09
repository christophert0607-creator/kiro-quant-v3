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
