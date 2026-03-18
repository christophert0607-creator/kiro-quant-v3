from __future__ import annotations

import io
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from v3_pipeline.data.downloader import HistoricalDataDownloader
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator
from v3_pipeline.models.brain import KiroLSTM

PATTERN_LABELS = [
    "Unknown",
    "HeadShoulderTop",
    "DoubleBottom",
    "UpTrend",
    "DownTrend",
    "Reversal",
    "VolumePulse",
    "Triangle",
]

REQUIRED_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]


def _build_stderr_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


@dataclass
class TrainingConfig:
    lookback: int = 60
    target_col: str = "Close"
    batch_size: int = 32
    epochs: int = 10
    lr: float = 1e-3
    hidden_dim: int = 64
    num_layers: int = 2
    dropout: float = 0.2
    output_dim: int = 1


@dataclass
class GlobalPretrainConfig:
    storage_dir: Path = Path("v3_pipeline/data/storage/base_10y")
    lookback: int = 60
    hidden_dim: int = 96
    num_layers: int = 2
    dropout: float = 0.2
    output_dim: int = 1
    attention_heads: int = 4


class DataPreparer:
    """Convert OHLCV+indicator DataFrames into normalized LSTM tensors."""

    def __init__(self, lookback: int = 60, target_col: str = "Close") -> None:
        self.lookback = lookback
        self.target_col = target_col
        self.logger = _build_stderr_logger(self.__class__.__name__)
        self._ensure_sanitization_logger()
        self.feature_columns: Optional[List[str]] = None
        self.feature_mins: Optional[pd.Series] = None
        self.feature_maxs: Optional[pd.Series] = None
        self.target_min: Optional[float] = None
        self.target_max: Optional[float] = None

    def _ensure_sanitization_logger(self) -> None:
        log_path = Path("v3_pipeline/logs/data_sanitization.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        resolved = log_path.resolve()
        for handler in self.logger.handlers:
            if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename).resolve() == resolved:
                return
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

    def _log_sanitize_step(self, step: str, before_rows: int, after_rows: int, df: pd.DataFrame) -> None:
        diff = after_rows - before_rows
        self.logger.info(
            "[Sanitize Log] %s: rows_before=%d, rows_after=%d, diff=%+d",
            step,
            before_rows,
            after_rows,
            diff,
        )
        if after_rows == 0:
            self._dump_sanitize_snapshot(step, df)

    def _dump_sanitize_snapshot(self, step: str, df: pd.DataFrame) -> None:
        buffer = io.StringIO()
        df.info(buf=buffer)
        self.logger.error("[Sanitize Log] %s -> df.info():\n%s", step, buffer.getvalue())
        self.logger.error("[Sanitize Log] %s -> isna().sum():\n%s", step, df.isna().sum().to_string())

    @property
    def is_fitted(self) -> bool:
        return (
            self.feature_columns is not None
            and self.feature_mins is not None
            and self.feature_maxs is not None
            and self.target_min is not None
            and self.target_max is not None
        )

    def fit_transform(self, frame: pd.DataFrame) -> Tuple[torch.Tensor, torch.Tensor]:
        clean = self._sanitize(frame)
        features = clean.drop(columns=["Date", "data_source"], errors="ignore").copy()

        if self.target_col not in features.columns:
            raise ValueError(f"target_col '{self.target_col}' not in data columns")

        self.feature_columns = features.columns.tolist()
        self.feature_mins = features.min(axis=0)
        self.feature_maxs = features.max(axis=0)
        scaled_features = self._minmax_scale(features, self.feature_mins, self.feature_maxs)

        target_series = clean[self.target_col].astype(float)
        self.target_min = float(target_series.min())
        self.target_max = float(target_series.max())
        scaled_target = self._scale_target(target_series)

        x, y = self._windowize(scaled_features.values, scaled_target.values)
        self.logger.info("Prepared tensors with X=%s and y=%s", tuple(x.shape), tuple(y.shape))
        return x, y

    def transform_for_inference(self, frame: pd.DataFrame) -> torch.Tensor:
        if not self.is_fitted:
            raise RuntimeError("DataPreparer is not fitted. Call fit_transform() first.")

        clean = frame.copy()
        clean["Date"] = pd.to_datetime(clean["Date"], errors="coerce")
        clean = (
            clean.dropna(subset=["Date"])
            .sort_values("Date")
            .drop_duplicates(subset=["Date"])
            .reset_index(drop=True)
        )

        numeric_cols = [c for c in clean.columns if c != "Date"]
        for col in numeric_cols:
            clean[col] = pd.to_numeric(clean[col], errors="coerce")

        inference_frame = clean.copy()
        inference_frame[numeric_cols] = inference_frame[numeric_cols].ffill().bfill()
        inference_frame[numeric_cols] = inference_frame[numeric_cols].fillna(0.0)

        features = inference_frame.drop(columns=["Date"]).copy()
        missing = [c for c in self.feature_columns if c not in features.columns]
        if missing:
            # Add missing columns with 0.0 (neutral value) - consistent with _sanitize behavior
            for col in missing:
                features[col] = 0.0
            self.logger.warning("Added %d missing feature columns with 0.0: %s", len(missing), missing)

        ordered = features[self.feature_columns]
        scaled = self._minmax_scale(ordered, self.feature_mins, self.feature_maxs)

        if len(scaled) < self.lookback:
            raise ValueError(f"Need at least lookback={self.lookback} rows, got {len(scaled)}")

        window = scaled.iloc[-self.lookback :].values.astype(np.float32)
        window = np.nan_to_num(window, nan=0.0, posinf=1.0, neginf=0.0)
        if np.isnan(window).any():
            raise RuntimeError("Hard-Fill failed: inference tensor still contains NaN")
        return torch.tensor(window).unsqueeze(0)

    def inverse_scale_target(self, value: float) -> float:
        if self.target_min is None or self.target_max is None:
            return value
        span = self.target_max - self.target_min
        if span == 0:
            return self.target_min
        return float(value * span + self.target_min)

    def _sanitize(self, frame: pd.DataFrame) -> pd.DataFrame:
        missing = [col for col in REQUIRED_COLUMNS if col not in frame.columns]
        if missing:
            raise ValueError(f"Input frame missing required OHLCV columns: {missing}")

        clean = frame.copy()
        if clean.empty:
            self._log_sanitize_step("input_empty", 0, 0, clean)
            raise ValueError("Input frame is empty after initial load.")

        clean["Date"] = pd.to_datetime(clean["Date"], errors="coerce")

        before = len(clean)
        clean["Date"] = clean["Date"].ffill()
        self._log_sanitize_step("ffill_date", before, len(clean), clean)

        before = len(clean)
        clean["Date"] = clean["Date"].bfill()
        self._log_sanitize_step("bfill_date", before, len(clean), clean)

        if clean["Date"].isna().all():
            self._dump_sanitize_snapshot("date_all_nan", clean)
            raise ValueError("Date column could not be parsed; all values are NaT.")

        drop_cols: list[str] = []
        for col in clean.columns:
            if col == "Date":
                continue
            series = clean[col]
            if pd.api.types.is_numeric_dtype(series):
                continue
            coerced = pd.to_numeric(series, errors="coerce")
            if coerced.notna().sum() == 0:
                drop_cols.append(col)
            else:
                clean[col] = coerced

        if drop_cols:
            before = len(clean)
            clean = clean.drop(columns=drop_cols, errors="ignore")
            self._log_sanitize_step(f"drop_non_numeric({','.join(drop_cols)})", before, len(clean), clean)

        numeric_cols = [c for c in clean.columns if c != "Date"]
        for col in numeric_cols:
            clean[col] = pd.to_numeric(clean[col], errors="coerce")
            # CRITICAL FIX: Replace INF with NaN so they get filled by ffill/bfill instead of poisoning min/max
            clean[col] = clean[col].replace([np.inf, -np.inf], np.nan)

        before = len(clean)
        clean = clean.sort_values("Date").drop_duplicates(subset=["Date"]).reset_index(drop=True)
        if len(clean) != before:
            self._log_sanitize_step("drop_duplicate_dates", before, len(clean), clean)

        if self.feature_columns:
            desired = ["Date"] + [c for c in self.feature_columns if c != "Date"]
            missing_cols = [c for c in desired if c not in clean.columns and c != "Date"]
            if missing_cols:
                for col in missing_cols:
                    clean[col] = 0.0
                self._log_sanitize_step(
                    f"add_missing_columns({','.join(missing_cols)})",
                    len(clean),
                    len(clean),
                    clean,
                )

            extra_cols = [c for c in clean.columns if c not in desired]
            if extra_cols:
                before = len(clean)
                clean = clean.drop(columns=extra_cols, errors="ignore")
                self._log_sanitize_step(f"drop_extra_columns({','.join(extra_cols)})", before, len(clean), clean)

            clean = clean[desired]
        else:
            clean = clean[["Date"] + [c for c in clean.columns if c != "Date"]]

        numeric_cols = [c for c in clean.columns if c != "Date"]
        if numeric_cols:
            before = len(clean)
            clean[numeric_cols] = clean[numeric_cols].ffill()
            self._log_sanitize_step("ffill_numeric", before, len(clean), clean)

            before = len(clean)
            clean[numeric_cols] = clean[numeric_cols].bfill()
            self._log_sanitize_step("bfill_numeric", before, len(clean), clean)

            before = len(clean)
            clean[numeric_cols] = clean[numeric_cols].fillna(0.0)
            self._log_sanitize_step("fillna_numeric", before, len(clean), clean)

        if clean.isna().any().any():
            self._dump_sanitize_snapshot("post_fill_nan", clean)

        if len(clean) <= self.lookback:
            raise ValueError(
                f"Not enough clean rows ({len(clean)}) for lookback={self.lookback}."
            )
        return clean

    def _windowize(self, feature_array: np.ndarray, target_array: np.ndarray) -> Tuple[torch.Tensor, torch.Tensor]:
        x_list, y_list = [], []
        for idx in range(self.lookback, len(feature_array)):
            x_list.append(feature_array[idx - self.lookback : idx])
            y_list.append(target_array[idx])

        x = torch.tensor(np.asarray(x_list), dtype=torch.float32)
        y = torch.tensor(np.asarray(y_list).reshape(-1, 1), dtype=torch.float32)
        return x, y

    @staticmethod
    def _minmax_scale(values: pd.DataFrame, mins: pd.Series, maxs: pd.Series) -> pd.DataFrame:
        denom = (maxs - mins).replace(0, 1.0)
        scaled = (values - mins) / denom
        return scaled.clip(lower=0.0, upper=1.0)

    def _scale_target(self, target: pd.Series) -> pd.Series:
        if self.target_min is None or self.target_max is None:
            raise RuntimeError("Target scaler not initialized")
        span = self.target_max - self.target_min
        if span == 0:
            return pd.Series(np.zeros(len(target)), index=target.index, dtype=float)
        return (target - self.target_min) / span


class AttentiveKiroLSTM(nn.Module):
    """LSTM + Self-Attention head for global pre-training regime."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = 96,
        num_layers: int = 2,
        dropout: float = 0.2,
        output_dim: int = 1,
        attention_heads: int = 4,
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
        self.attn = nn.MultiheadAttention(hidden_dim, num_heads=max(1, attention_heads), batch_first=True)
        self.norm = nn.LayerNorm(hidden_dim)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_dim, output_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        seq, _ = self.lstm(x)
        attn_out, _ = self.attn(seq, seq, seq, need_weights=False)
        fused = self.norm(seq + attn_out)
        last = fused[:, -1, :]
        return self.fc(self.dropout(last))


class ModelManager:
    def __init__(
        self,
        model: nn.Module,
        data_preparer: DataPreparer,
        device: Optional[str] = None,
        model_dir: str = "v3_pipeline/models/trained_models",
    ) -> None:
        self.logger = _build_stderr_logger(self.__class__.__name__)
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.model = model.to(self.device)
        self.data_preparer = data_preparer
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def build_global_pretraining_manager(cls, sample_frame: pd.DataFrame, config: Optional[GlobalPretrainConfig] = None) -> "ModelManager":
        cfg = config or GlobalPretrainConfig()
        preparer = DataPreparer(lookback=cfg.lookback, target_col="Close")
        x, _ = preparer.fit_transform(sample_frame)
        model = AttentiveKiroLSTM(
            input_dim=x.shape[-1],
            hidden_dim=cfg.hidden_dim,
            num_layers=cfg.num_layers,
            dropout=cfg.dropout,
            output_dim=cfg.output_dim,
            attention_heads=cfg.attention_heads,
        )
        return cls(model=model, data_preparer=preparer)

    @staticmethod
    def load_base_10y_frames(storage_dir: Path) -> Dict[str, pd.DataFrame]:
        frames: Dict[str, pd.DataFrame] = {}
        if not storage_dir.exists():
            return frames
        for pq in sorted(storage_dir.glob("*.parquet")):
            try:
                df = pd.read_parquet(pq)
                if not df.empty:
                    frames[pq.stem] = df
            except Exception:
                continue
        return frames

    def global_pretrain_from_storage(
        self,
        storage_dir: Path,
        epochs: int = 10,
        lr: float = 1e-3,
        batch_size: int = 64,
    ) -> List[float]:
        frames = self.load_base_10y_frames(storage_dir)
        if not frames:
            raise RuntimeError(f"No parquet data found under {storage_dir}")

        combined = pd.concat(frames.values(), ignore_index=True).sort_values("Date").reset_index(drop=True)
        dataloader = self.build_dataloader(combined, batch_size=batch_size, shuffle=True)
        self.logger.info("Global pre-training on %d symbols, rows=%d", len(frames), len(combined))
        return self.train(dataloader, epochs=epochs, lr=lr)

    def build_dataloader(
        self,
        frame: pd.DataFrame,
        batch_size: int = 32,
        shuffle: bool = True,
    ) -> DataLoader:
        x, y = self.data_preparer.fit_transform(frame)
        dataset = TensorDataset(x, y)
        return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)

    def train(self, dataloader: DataLoader, epochs: int = 10, lr: float = 1e-3) -> List[float]:
        criterion = nn.MSELoss()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)

        losses: List[float] = []
        self.model.train()

        for epoch in range(1, epochs + 1):
            epoch_losses: List[float] = []
            for batch_x, batch_y in dataloader:
                batch_x = batch_x.to(self.device)
                batch_y = batch_y.to(self.device)

                optimizer.zero_grad()
                pred = self.model(batch_x)
                loss = criterion(pred, batch_y)
                loss.backward()
                optimizer.step()

                epoch_losses.append(float(loss.item()))

            mean_loss = float(np.mean(epoch_losses)) if epoch_losses else float("nan")
            losses.append(mean_loss)
            self.logger.info("Epoch %d/%d | MSE=%.6f", epoch, epochs, mean_loss)

        return losses

    def _ensure_model_input_dim(self, input_dim: int) -> None:
        lstm = getattr(self.model, "lstm", None)
        current_dim = int(getattr(lstm, "input_size", input_dim)) if lstm is not None else input_dim
        if current_dim == input_dim:
            return

        self.logger.warning("Input dim drift detected: model=%d new=%d. Rebuilding channels.", current_dim, input_dim)

        if isinstance(self.model, AttentiveKiroLSTM):
            rebuilt: nn.Module = AttentiveKiroLSTM(
                input_dim=input_dim,
                hidden_dim=int(self.model.lstm.hidden_size),
                num_layers=int(self.model.lstm.num_layers),
                dropout=float(self.model.dropout.p),
                output_dim=int(self.model.fc.out_features),
                attention_heads=int(self.model.attn.num_heads),
            ).to(self.device)
        else:
            rebuilt = KiroLSTM(
                input_dim=input_dim,
                hidden_dim=int(self.model.lstm.hidden_size),
                num_layers=int(self.model.lstm.num_layers),
                dropout=float(self.model.dropout.p),
                output_dim=int(self.model.fc.out_features),
            ).to(self.device)

        self.model = rebuilt

    def predict(self, latest_data_window: pd.DataFrame, data_preparer: Optional[DataPreparer] = None) -> float:
        active_preparer = data_preparer or self.data_preparer
        self.model.eval()
        with torch.no_grad():
            x = active_preparer.transform_for_inference(latest_data_window).to(self.device)
            self._ensure_model_input_dim(int(x.shape[-1]))
            model_out = self.model(x)
            scaled_tensor = model_out[0] if isinstance(model_out, tuple) else model_out
            scaled_pred = float(scaled_tensor.cpu().numpy().ravel()[0])
            pred = active_preparer.inverse_scale_target(scaled_pred)
            
            # FINAL SAFETY CHECK: Ensure prediction is not NaN or Inf
            if not np.isfinite(pred):
                self.logger.warning("Prediction is non-finite (NaN/Inf). Scaled=%.6f. Falling back to latest price.", scaled_pred)
                try:
                    pred = float(latest_data_window.iloc[-1][active_preparer.target_col])
                except:
                    pred = 0.0 # Extreme fallback
            
            self.logger.info("Prediction (scaled=%.6f, inverse=%.6f, input_dim=%d)", scaled_pred, pred, int(x.shape[-1]))
            return pred

    def _classify_pattern_rule_based(self, df: pd.DataFrame) -> dict:
        """
        Rule-based pattern classification using technical indicators.
        Used as fallback when model doesn't return pattern logits.
        """
        if len(df) < 20:
            return {"pattern": "Unknown", "confidence": 0.0, "probs": {"Unknown": 1.0}}
        
        # Get latest values
        latest = df.iloc[-1]
        prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-2]
        
        close = latest.get("Close", 0.0)
        high = latest.get("High", 0.0)
        low = latest.get("Low", 0.0)
        volume = latest.get("Volume", 0.0)
        
        # Get EMA values (use EMA_12 and EMA_26 if available)
        ema_fast = float(latest.get("EMA_12", close)) if not pd.isna(latest.get("EMA_12")) else close
        ema_slow = float(latest.get("EMA_26", close)) if not pd.isna(latest.get("EMA_26")) else close
        
        # Get Bollinger Bands
        bb_upper = float(latest.get("BB_UPPER", close)) if not pd.isna(latest.get("BB_UPPER")) else close
        bb_lower = float(latest.get("BB_LOWER", close)) if not pd.isna(latest.get("BB_LOWER")) else close
        bb_middle = float(latest.get("BB_MIDDLE", close)) if not pd.isna(latest.get("BB_MIDDLE")) else close
        
        # Get ATR for volatility measurement
        atr = float(latest.get("ATR_14", 0.0)) if not pd.isna(latest.get("ATR_14")) else 0.0
        
        # Calculate average close for normalization
        avg_close = df["Close"].mean()
        atr_ratio = atr / avg_close if avg_close > 0 else 0
        
        # Volume analysis
        avg_volume = df["Volume"].rolling(20).mean().iloc[-1] if len(df) >= 20 else df["Volume"].mean()
        volume_ratio = volume / avg_volume if avg_volume > 0 else 1.0
        
        # Pattern detection scores
        pattern_scores = {}
        
        # 1. Uptrend: EMA fast > EMA slow, price above EMA
        if ema_fast > ema_slow and close > ema_fast:
            trend_strength = min(1.0, (ema_fast - ema_slow) / ema_slow * 100 if ema_slow > 0 else 0)
            price_above_ema = min(1.0, (close - ema_fast) / ema_fast * 100 if ema_fast > 0 else 0)
            pattern_scores["UpTrend"] = 0.5 + 0.3 * trend_strength + 0.2 * price_above_ema
        
        # 2. Downtrend: EMA fast < EMA slow, price below EMA
        if ema_fast < ema_slow and close < ema_fast:
            trend_strength = min(1.0, (ema_slow - ema_fast) / ema_slow * 100 if ema_slow > 0 else 0)
            price_below_ema = min(1.0, (ema_fast - close) / ema_fast * 100 if ema_fast > 0 else 0)
            pattern_scores["DownTrend"] = 0.5 + 0.3 * trend_strength + 0.2 * price_below_ema
        
        # 3. Consolidation: Low volatility, price between bands, near middle
        bb_width = (bb_upper - bb_lower) / bb_middle if bb_middle > 0 else 0
        if bb_width < 0.05 and abs(close - bb_middle) / bb_middle < 0.02 if bb_middle > 0 else False:
            pattern_scores["Consolidation"] = 0.7 + 0.3 * (1.0 - bb_width * 20)
        
        # 4. Breakout: Strong volume, price突破 recent high
        recent_high = df["High"].rolling(20).max().iloc[-2] if len(df) >= 2 else df["High"].max()
        recent_low = df["Low"].rolling(20).min().iloc[-2] if len(df) >= 2 else df["Low"].min()
        
        if high > recent_high and volume_ratio > 1.5:
            breakout_strength = min(1.0, (high - recent_high) / recent_high * 100 if recent_high > 0 else 0)
            pattern_scores["Breakout"] = 0.5 + 0.3 * min(1.0, (volume_ratio - 1.5) / 2.0) + 0.2 * breakout_strength
        elif low < recent_low and volume_ratio > 1.5:
            breakdown_strength = min(1.0, (recent_low - low) / recent_low * 100 if recent_low > 0 else 0)
            pattern_scores["Breakout"] = 0.5 + 0.3 * min(1.0, (volume_ratio - 1.5) / 2.0) + 0.2 * breakdown_strength
        
        # 5. Volatility Spike: High ATR or wide Bollinger Bands
        if bb_width > 0.08 or atr_ratio > 0.03:
            vol_score = max(bb_width * 10, atr_ratio * 30)
            pattern_scores["VolatilitySpike"] = min(0.95, 0.5 + vol_score)
        
        # Normalize scores to probabilities
        if pattern_scores:
            total_score = sum(pattern_scores.values())
            probs = {k: round(v / total_score, 4) for k, v in pattern_scores.items()}
            best_pattern = max(pattern_scores, key=pattern_scores.get)
            confidence = pattern_scores[best_pattern] / total_score
            
            # Ensure all pattern types have a probability (add Unknown for remaining)
            remaining_prob = 1.0 - sum(probs.values())
            if remaining_prob > 0.001:
                probs["Unknown"] = round(remaining_prob, 4)
            
            return {
                "pattern": best_pattern,
                "confidence": round(confidence, 4),
                "probs": probs,
            }
        
        return {"pattern": "Unknown", "confidence": 0.0, "probs": {"Unknown": 1.0}}

    def predict_pattern(self, latest_data_window: pd.DataFrame, data_preparer: Optional[DataPreparer] = None) -> dict:
        active_preparer = data_preparer or self.data_preparer
        self.model.eval()
        with torch.no_grad():
            x = active_preparer.transform_for_inference(latest_data_window).to(self.device)
            self._ensure_model_input_dim(int(x.shape[-1]))
            model_out = self.model(x)
            if not isinstance(model_out, tuple) or len(model_out) != 2:
                # Model doesn't support pattern classification, use rule-based fallback
                return self._classify_pattern_rule_based(latest_data_window)

            _, pattern_logits = model_out
            probs = F.softmax(pattern_logits, dim=-1).cpu().numpy().ravel()
            labels = PATTERN_LABELS[: len(probs)]
            best_idx = int(np.argmax(probs)) if len(probs) else 0
            best_label = labels[best_idx] if best_idx < len(labels) else "Unknown"
            best_conf = float(probs[best_idx]) if len(probs) else 0.0
            return {
                "pattern": best_label,
                "confidence": best_conf,
                "probs": {label: float(p) for label, p in zip(labels, probs)},
            }

    def save(self, model_name: str) -> Path:
        target = self.model_dir / f"{model_name}.pth"
        payload = {
            "model_state_dict": self.model.state_dict(),
            "lookback": self.data_preparer.lookback,
            "target_col": self.data_preparer.target_col,
            "feature_columns": self.data_preparer.feature_columns,
            "feature_mins": self.data_preparer.feature_mins.to_dict() if self.data_preparer.feature_mins is not None else None,
            "feature_maxs": self.data_preparer.feature_maxs.to_dict() if self.data_preparer.feature_maxs is not None else None,
            "target_min": self.data_preparer.target_min,
            "target_max": self.data_preparer.target_max,
            "model_class": self.model.__class__.__name__,
        }
        torch.save(payload, target)
        self.logger.info("Saved model checkpoint to %s", target)
        return target

    def load(self, model_name: str) -> None:
        source = self.model_dir / f"{model_name}.pth"
        payload = torch.load(source, map_location=self.device)
        self.model.load_state_dict(payload["model_state_dict"])

        self.data_preparer.lookback = int(payload.get("lookback", self.data_preparer.lookback))
        self.data_preparer.target_col = str(payload.get("target_col", self.data_preparer.target_col))

        feature_columns = payload.get("feature_columns")
        if feature_columns is not None:
            self.data_preparer.feature_columns = list(feature_columns)

        feature_mins = payload.get("feature_mins")
        feature_maxs = payload.get("feature_maxs")
        if feature_mins is not None:
            self.data_preparer.feature_mins = pd.Series(feature_mins)
        if feature_maxs is not None:
            self.data_preparer.feature_maxs = pd.Series(feature_maxs)

        self.data_preparer.target_min = payload.get("target_min")
        self.data_preparer.target_max = payload.get("target_max")

        self.model.eval()
        self.logger.info("Loaded model checkpoint from %s", source)


def train_symbol_pipeline(
    symbol: str,
    start_date: str,
    end_date: str,
    config: Optional[TrainingConfig] = None,
) -> Tuple[ModelManager, List[float], float]:
    cfg = config or TrainingConfig()

    logger = _build_stderr_logger("KiroTrainPipeline")
    downloader = HistoricalDataDownloader()
    feature_gen = TechnicalIndicatorGenerator()

    logger.info("Running integrated train pipeline for %s", symbol)
    ohlcv = downloader.fetch_history(symbol, start_date, end_date, interval="1d", save=True)
    featured = feature_gen.generate(ohlcv)
    featured = featured.ffill().bfill().dropna().reset_index(drop=True)

    preparer = DataPreparer(lookback=cfg.lookback, target_col=cfg.target_col)
    sample_x, sample_y = preparer.fit_transform(featured)

    model = KiroLSTM(
        input_dim=sample_x.shape[-1],
        hidden_dim=cfg.hidden_dim,
        num_layers=cfg.num_layers,
        dropout=cfg.dropout,
        output_dim=cfg.output_dim,
    )

    manager = ModelManager(model=model, data_preparer=preparer)
    dataloader = DataLoader(TensorDataset(sample_x, sample_y), batch_size=cfg.batch_size, shuffle=True)

    losses = manager.train(dataloader, epochs=cfg.epochs, lr=cfg.lr)
    manager.save(f"{symbol}_kiro_lstm")
    prediction = manager.predict(featured)

    return manager, losses, prediction

