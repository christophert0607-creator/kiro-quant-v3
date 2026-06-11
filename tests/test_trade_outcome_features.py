import torch

from v3_pipeline.models.manager import AttentiveKiroLSTM
from v3_pipeline.models.trade_outcome_features import build_trade_outcome_features


def test_attentive_lstm_can_return_hidden_state_without_breaking_default_forward():
    model = AttentiveKiroLSTM(input_dim=5, hidden_dim=16, num_layers=1, dropout=0.0, output_dim=1, attention_heads=4)
    x = torch.randn(2, 10, 5)

    default_out = model(x)
    pred, hidden = model(x, return_hidden=True)

    assert default_out.shape == (2, 1)
    assert pred.shape == (2, 1)
    assert hidden.shape == (2, 16)


def test_trade_outcome_feature_builder_combines_hidden_cl_and_context():
    hidden = [0.1] * 4
    cl = [0.2] * 3
    result = build_trade_outcome_features(
        symbol="0700.HK",
        market="HK",
        lstm_hidden=hidden,
        cl_embedding=cl,
        indicators={"RSI_14": 40.0, "MACD_HIST": 0.2},
        risk_context={"trade_quality_score": 0.7, "confidence": 0.8},
    )

    assert result.source_flags["hidden_available"] is True
    assert result.source_flags["cl_available"] is True
    assert len(result.x) == len(result.feature_names)
    assert "market_HK" in result.feature_names
    assert "risk_trade_quality_score" in result.feature_names
