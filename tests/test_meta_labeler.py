"""
Unit tests for self_learn.meta_labeler — meta_005
==================================================
Tests the Meta-Labeling decision engine:
- Decision.NO_DATA when no closed trade history
- Decision.CONFIRM with high directional accuracy
- Decision.REJECT in uncertain zone
- Decision.REVERSE with poor directional accuracy
- High-confidence override logic

Safe: uses mock DB queries, no live trading, no risk logic.
"""

import pytest
from unittest.mock import patch, MagicMock


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _make_acc(symbol: str, mae: float, dir_acc: float, sample_size: int = 20):
    """Build a mock SymbolAccuracy."""
    from self_learn.meta_labeler import SymbolAccuracy
    return SymbolAccuracy(symbol=symbol, mae=mae, directional_accuracy=dir_acc, sample_size=sample_size)


def _make_ctx(symbol: str, action: str, entry: float,
              predicted: float, confidence: float, acc=None):
    """Build a mock SignalContext."""
    from self_learn.meta_labeler import SignalContext
    return SignalContext(
        symbol=symbol, action=action, entry_price=entry,
        predicted_price=predicted, confidence=confidence,
        symbol_accuracy=acc,
    )


# ─── Tests ─────────────────────────────────────────────────────────────────────

class TestDecisionNO_DATA:
    """meta_005a — NO_DATA when no historical outcomes exist."""

    @patch("self_learn.meta_labeler.compute_symbol_accuracy")
    def test_no_history_yields_no_data(self, mock_compute):
        from self_learn.meta_labeler import should_take_trade, Decision

        mock_compute.return_value = _make_acc("0005.HK", mae=0.0, dir_acc=0.5)

        result = should_take_trade(
            symbol="0005.HK", action="BUY",
            entry_price=50.0, predicted_price=52.0, confidence=0.70,
        )

        assert result.decision == Decision.NO_DATA
        assert result.overrides_base_signal is False
        assert "cannot evaluate" in result.reason.lower()

    @patch("self_learn.meta_labeler.compute_symbol_accuracy")
    def test_none_accuracy_yields_no_data(self, mock_compute):
        from self_learn.meta_labeler import should_take_trade, Decision

        mock_compute.return_value = None

        result = should_take_trade(
            symbol="9988.HK", action="SELL",
            entry_price=120.0, predicted_price=115.0, confidence=0.55,
        )

        assert result.decision == Decision.NO_DATA


class TestDecisionCONFIRM:
    """meta_005b — CONFIRM when directional accuracy is high."""

    @patch("self_learn.meta_labeler.compute_symbol_accuracy")
    def test_high_dir_acc_confirms(self, mock_compute):
        from self_learn.meta_labeler import should_take_trade, Decision

        # 60% directional accuracy — above 0.55 threshold
        mock_compute.return_value = _make_acc("0700.HK", mae=2.5, dir_acc=0.60)

        result = should_take_trade(
            symbol="0700.HK", action="BUY",
            entry_price=400.0, predicted_price=410.0, confidence=0.60,
        )

        assert result.decision == Decision.CONFIRM
        assert result.overrides_base_signal is False
        assert result.symbol_accuracy is not None
        assert result.symbol_accuracy.symbol == "0700.HK"

    @patch("self_learn.meta_labeler.compute_symbol_accuracy")
    def test_high_dir_acc_high_mae_still_confirms(self, mock_compute):
        from self_learn.meta_labeler import should_take_trade, Decision

        # High dir_acc but MAE above threshold — still confirm, reason notes high MAE
        mock_compute.return_value = _make_acc("NVDA", mae=8.0, dir_acc=0.62)

        result = should_take_trade(
            symbol="NVDA", action="BUY",
            entry_price=100.0, predicted_price=108.0, confidence=0.60,
        )

        assert result.decision == Decision.CONFIRM
        assert "high MAE" in result.reason


class TestDecisionREJECT:
    """meta_005c — REJECT in the uncertain middle zone."""

    @patch("self_learn.meta_labeler.compute_symbol_accuracy")
    def test_middle_dir_acc_rejects(self, mock_compute):
        from self_learn.meta_labeler import should_take_trade, Decision

        # 0.52 dir_acc — below confirm (0.55) but above reverse (0.40)
        mock_compute.return_value = _make_acc("TSLA", mae=3.0, dir_acc=0.52)

        result = should_take_trade(
            symbol="TSLA", action="BUY",
            entry_price=200.0, predicted_price=205.0, confidence=0.50,
        )

        assert result.decision == Decision.REJECT
        assert result.overrides_base_signal is False
        assert "uncertain zone" in result.reason.lower()


class TestDecisionREVERSE:
    """meta_005d — REVERSE when directional accuracy is very poor."""

    @patch("self_learn.meta_labeler.compute_symbol_accuracy")
    def test_poor_dir_acc_reverses(self, mock_compute):
        from self_learn.meta_labeler import should_take_trade, Decision

        # 0.35 dir_acc — at or below 0.40 reverse threshold
        mock_compute.return_value = _make_acc("AMD", mae=5.0, dir_acc=0.35)

        result = should_take_trade(
            symbol="AMD", action="BUY",
            entry_price=150.0, predicted_price=155.0, confidence=0.55,
        )

        assert result.decision == Decision.REVERSE
        assert result.overrides_base_signal is True

    @patch("self_learn.meta_labeler.compute_symbol_accuracy")
    def test_very_poor_dir_acc_strong_reversal_confidence(self, mock_compute):
        from self_learn.meta_labeler import should_take_trade, Decision

        # 0.20 dir_acc — very poor, strong reversal confidence
        mock_compute.return_value = _make_acc("META", mae=6.0, dir_acc=0.20)

        result = should_take_trade(
            symbol="META", action="BUY",
            entry_price=500.0, predicted_price=510.0, confidence=0.60,
        )

        assert result.decision == Decision.REVERSE
        # reversal confidence = 0.5 - 0.20 = 0.30
        assert result.confidence == pytest.approx(0.30)


class TestConfidenceOverride:
    """meta_005e — High model confidence can override directional accuracy."""

    @patch("self_learn.meta_labeler.compute_symbol_accuracy")
    def test_high_confidence_overrides_weak_dir_acc(self, mock_compute):
        from self_learn.meta_labeler import should_take_trade, Decision

        # High confidence (0.85) + decent dir_acc (0.57) → confirm via override
        mock_compute.return_value = _make_acc("AAPL", mae=1.5, dir_acc=0.57)

        result = should_take_trade(
            symbol="AAPL", action="BUY",
            entry_price=190.0, predicted_price=192.0, confidence=0.85,
        )

        assert result.decision == Decision.CONFIRM
        assert result.confidence == pytest.approx(0.85)
        assert "overrides" in result.reason.lower()


class TestGetMetaStats:
    """meta_005f — get_meta_stats() returns correct readiness state."""

    @patch("self_learn.meta_labeler.get_stats")
    def test_not_ready_under_20_outcomes(self, mock_get_stats):
        from self_learn.meta_labeler import get_meta_stats, DIR_ACC_CONFIRM_THRESHOLD

        mock_get_stats.return_value = {
            "total_predictions": 10000,
            "total_signals": 5,
            "open_signals": 5,
            "closed_signals": 0,
            "total_outcomes": 0,
        }

        stats = get_meta_stats()

        assert stats["ready"] is False
        assert stats["db_outcomes"] == 0
        assert stats["db_predictions"] == 10000
        assert stats["dir_acc_threshold_confirm"] == DIR_ACC_CONFIRM_THRESHOLD

    @patch("self_learn.meta_labeler.get_stats")
    def test_ready_at_20_outcomes(self, mock_get_stats):
        from self_learn.meta_labeler import get_meta_stats

        mock_get_stats.return_value = {
            "total_predictions": 50000,
            "total_signals": 50,
            "open_signals": 30,
            "closed_signals": 20,
            "total_outcomes": 20,
        }

        stats = get_meta_stats()

        assert stats["ready"] is True
        assert stats["db_outcomes"] == 20