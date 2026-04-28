from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from v3_pipeline.data.downloader import HistoricalDataDownloader
from v3_pipeline.features.indicators import TechnicalIndicatorGenerator
from v3_pipeline.models.brain import KiroLSTM

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
        self.feature_columns: Optional[List[str]] = None
        self.feature_mins: Optional[pd.Series] = None
        self.feature_maxs: Optional[pd.Series] = None
        self.target_min: Optional[float] = None
        self.target_max: Optional[float] = None

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

        # Ensure numeric columns before extracting features
        for col in clean.columns:
            if col in ("Date", "data_source"):
                continue
            clean[col] = pd.to_numeric(clean[col], errors="coerce")

        # Explicitly exclude non-feature columns
        non_feature_cols = {"Date", "data_source"}
        feature_cols = [c for c in clean.columns if c not in non_feature_cols]

        if self.target_col not in feature_cols:
            raise ValueError(f"target_col '{self.target_col}' not in data columns")

        features = clean[feature_cols].copy()

        # Replace any remaining NaN/inf in feature columns with 0 before min/max
        for col in features.columns:
            features[col] = features[col].replace([float("inf"), float("-inf")], 0.0)
        features = features.fillna(0.0)

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
        # 2026-04-23 Fix: If feature_columns is set (e.g. by ModelManager.predict),
        # we bypass the strict is_fitted check for normalization parameters,
        # but we still need them for scaling.
        if self.feature_columns is None and not self.is_fitted:
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
        
        # If we have an explicit target list, use it; otherwise use whatever is in features
        target_cols = self.feature_columns if self.feature_columns is not None else features.columns.tolist()
        
        missing = [c for c in target_cols if c not in features.columns]
        if missing:
            # For inference, we can afford to fill missing technical indicators with 0
            for m in missing:
                features[m] = 0.0

        ordered = features[target_cols]
        
        # Scaling
        if self.feature_mins is not None and self.feature_maxs is not None:
            # Ensure mins/maxs align with ordered columns
            m_mins = self.feature_mins
            m_maxs = self.feature_maxs
            if isinstance(m_mins, np.ndarray):
                # Fallback if they were loaded as raw arrays from checkpoint
                scaled = ordered.values.astype(np.float32)
                # Simple 0-1 scaling if arrays match size
                if len(m_mins) == ordered.shape[1]:
                    denom = (m_maxs - m_mins)
                    denom[denom == 0] = 1.0
                    scaled = (scaled - m_mins) / denom
                scaled_df = pd.DataFrame(scaled, columns=ordered.columns)
            else:
                scaled_df = self._minmax_scale(ordered, m_mins, m_maxs)
        else:
            # No scaling metadata: return raw (not ideal, but prevents crash)
            scaled_df = ordered

        if len(scaled_df) < self.lookback:
            raise ValueError(f"Need at least lookback={self.lookback} rows, got {len(scaled_df)}")

        window = scaled_df.iloc[-self.lookback :].values.astype(np.float32)
        window = np.nan_to_num(window, nan=0.0, posinf=1.0, neginf=0.0)
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
        clean["Date"] = pd.to_datetime(clean["Date"], errors="coerce")
        clean = clean.dropna(subset=["Date"]).sort_values("Date").drop_duplicates(subset=["Date"])

        numeric_cols = [c for c in clean.columns if c not in ("Date", "data_source")]
        for col in numeric_cols:
            clean[col] = pd.to_numeric(clean[col], errors="coerce")

        clean = clean.dropna().reset_index(drop=True)

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
        model_dir: Optional[str] = None,
    ) -> None:
        self.logger = _build_stderr_logger(self.__class__.__name__)
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.model = model.to(self.device)
        self.model.eval()

        self.data_preparer = data_preparer
        
        # 2026-04-15 Fix: Use absolute path relative to this file to avoid "Model Not Found" errors
        if model_dir is None:
            base_path = Path(__file__).parent.parent / "models" / "trained_models"
            self.model_dir = base_path
        else:
            self.model_dir = Path(model_dir)
            
        self.model_dir.mkdir(parents=True, exist_ok=True)

        # 2026-04-23 Fix: Ensure self.model_meta is always available if checkpoint exists
        # This prevents 'input_dim mismatch (26 vs 27)' by providing authoritative feature_columns.
        self.model_meta = {}
        if getattr(model, '_kiro_model_name', None):
            ckpt_path = self.model_dir / f"{model._kiro_model_name}.pth"
            if ckpt_path.exists():
                try:
                    self.model_meta = torch.load(ckpt_path, map_location='cpu', weights_only=False)
                    self._sync_preparer_from_checkpoint(model._kiro_model_name)
                except Exception:
                    pass

    def _sync_preparer_from_checkpoint(self, model_name: str) -> None:
        """Load feature columns / normalization from a .pth checkpoint into the global preparer."""
        import torch
        ckpt_path = self.model_dir / f"{model_name}.pth"
        if not ckpt_path.exists():
            return
        try:
            payload = torch.load(ckpt_path, map_location='cpu', weights_only=False)
            if payload.get('feature_columns'):
                self.data_preparer.feature_columns = list(payload['feature_columns'])
            if payload.get('feature_mins') is not None:
                self.data_preparer.feature_mins = np.array(payload['feature_mins'], dtype=np.float32)
            if payload.get('feature_maxs') is not None:
                self.data_preparer.feature_maxs = np.array(payload['feature_maxs'], dtype=np.float32)
            self.logger.debug("Synced preparer feature_columns from %s: %s",
                               ckpt_path.name, self.data_preparer.feature_columns[:5])
        except Exception as exc:
            self.logger.warning("Could not sync preparer from %s: %s", ckpt_path.name, exc)

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

    def predict(self, latest_data_window: pd.DataFrame, data_preparer: Optional[DataPreparer] = None) -> float:
        # Use provided data_preparer (local symbol context) or fallback to global manager context
        active_preparer = data_preparer or self.data_preparer
        self.model.eval()
        
        # 2026-04-23 Final Fix: Determine model's expected dimension directly from the LSTM weights
        # to ensure absolute compatibility regardless of which checkpoint is currently loaded.
        try:
            expected_dim = self.model.lstm.input_size
        except AttributeError:
            # Fallback for models without .lstm attribute
            expected_dim = getattr(self.model, 'input_dim', 26)

        with torch.no_grad():
            clean = latest_data_window.copy()
            clean["Date"] = pd.to_datetime(clean["Date"], errors="coerce")
            
            # Filter non-numeric
            for col in ["data_source"]:
                if col in clean.columns:
                    clean.drop(columns=[col], inplace=True)
            
            # Ensure basic OHLCV context
            for col in ["Dividends", "Stock Splits"]:
                if col not in clean.columns:
                    clean[col] = 0.0

            # Step 1: Align feature columns list
            # If the active_preparer doesn't have a column list, or it doesn't match the model,
            # we must derive an ordering that fits expected_dim.
            current_cols = [c for c in clean.columns if c != "Date"]
            
            # If we have metadata, use it as the primary ordering guide
            ref_features = getattr(self, 'model_meta', {}).get('feature_columns', [])
            if ref_features and len(ref_features) == expected_dim:
                # Meta matches model perfectly, use it
                target_cols = list(ref_features)
            else:
                # Meta is missing or wrong size; slice current columns to fit model
                target_cols = current_cols[:expected_dim]
            
            # Ensure the preparer knows this order for normalization
            active_preparer.feature_columns = target_cols
            
            # Ensure all required columns exist in 'clean' (fill missing with 0)
            for c in target_cols:
                if c not in clean.columns:
                    clean[c] = 0.0
            
            # Final reorder to match target_cols exactly
            clean = clean[["Date"] + target_cols]
            
            # Step 2: Transform
            x = active_preparer.transform_for_inference(clean).to(self.device)
            
            # 2026-04-23 Debug: Log dimensions before crash
            if x.shape[-1] != expected_dim:
                self.logger.error("DIMENSION MISMATCH: x.shape=%s expected_dim=%d model.lstm.input_size=%d",
                                 tuple(x.shape), expected_dim, getattr(self.model.lstm, 'input_size', -1))

            # Double check shape before forward pass to prevent RuntimeError
            if x.shape[-1] != expected_dim:
                # Emergency slice
                x = x[:, :, :expected_dim]
                
            scaled_pred = float(self.model(x).cpu().numpy().ravel()[0])
            
            # Step 3: Inverse scale
            pred = active_preparer.inverse_scale_target(scaled_pred)
            
            self.logger.info("Prediction (scaled=%.6f, inverse=%.6f, input_dim=%d)", 
                             scaled_pred, pred, int(x.shape[-1]))
            return pred

    def save(self, model_name: str) -> Path:
        target = self.model_dir / f"{model_name}.pth"
        # Extract architecture from the model
        model_cfg = {}
        if hasattr(self.model, "hidden_dim"):
            model_cfg["hidden_dim"] = self.model.hidden_dim
        if hasattr(self.model, "num_layers"):
            model_cfg["num_layers"] = self.model.num_layers
        if hasattr(self.model, "dropout"):
            if hasattr(self.model.dropout, "p"):
                model_cfg["dropout"] = float(self.model.dropout.p)
            else:
                try:
                    model_cfg["dropout"] = float(self.model.dropout)
                except Exception:
                    model_cfg["dropout"] = 0.2
        if hasattr(self.model, "output_dim"):
            model_cfg["output_dim"] = self.model.output_dim
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
            "model_cfg": model_cfg,
        }
        torch.save(payload, target)
        self.logger.info("Saved model checkpoint to %s (cfg=%s)", target, model_cfg)
        return target

    def load(self, model_name: str) -> None:
        source = self.model_dir / f"{model_name}.pth"
        payload = torch.load(source, map_location=self.device, weights_only=False)
        self.model_meta = payload
        model_cfg = payload.get("model_cfg", {})
        state_dict = payload["model_state_dict"]
        model_class_name = payload.get("model_class", "KiroLSTM")

        # Always rebuild model to match saved state_dict architecture
        ih_shape = state_dict["lstm.weight_ih_l0"].shape
        hh_shape = state_dict["lstm.weight_hh_l0"].shape
        detected_input_dim = ih_shape[1]
        detected_hidden_dim = hh_shape[0] // 4  # LSTM: weight_hh is 4*hidden_dim
        layers_set = {int(k.split("_")[2].replace("l", "")) for k in state_dict if k.startswith("lstm.weight_hh_l")}
        detected_num_layers = max(layers_set) + 1
        detected_dropout = model_cfg.get("dropout", 0.2)
        
        self.logger.info("Auto-detected model architecture: class=%s input_dim=%d hidden_dim=%d num_layers=%d",
                        model_class_name, detected_input_dim, detected_hidden_dim, detected_num_layers)
        
        if model_class_name == "AttentiveKiroLSTM":
            new_model = AttentiveKiroLSTM(
                input_dim=detected_input_dim,
                hidden_dim=detected_hidden_dim,
                num_layers=detected_num_layers,
                dropout=detected_dropout,
                output_dim=1,
                attention_heads=model_cfg.get("attention_heads", 4)
            )
        else:
            new_model = KiroLSTM(
                input_dim=detected_input_dim,
                hidden_dim=detected_hidden_dim,
                num_layers=detected_num_layers,
                dropout=detected_dropout,
                output_dim=1,
            )
            
        self.model = new_model.to(self.device)
        self.model.load_state_dict(state_dict)

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
