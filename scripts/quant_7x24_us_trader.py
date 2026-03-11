#!/usr/bin/env python3
"""7x24 unattended quantitative trading system scaffold for US equities.

Features:
- timezone-aware phase scheduler (GMT+8 / Asia/Taipei)
- modular data collection, model training, pre-open prep, live trading, post-market reporting
- CUDA-aware PyTorch training with mixed precision and safe CPU fallback
- dry-run paper trading mode, rate limiting, structured logging, optional Slack/email alerts
- SQLite incremental storage for scalable dataset growth

Run:
  python scripts/quant_7x24_us_trader.py --config scripts/quant_7x24_config.example.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import smtplib
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

try:
    import requests
except Exception:  # optional dependency
    requests = None

try:
    import yfinance as yf
except Exception:  # optional dependency
    yf = None

try:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
except Exception:  # optional dependency
    torch = None
    nn = None
    DataLoader = None
    TensorDataset = None


class GracefulExit(SystemExit):
    pass


@dataclass
class PhaseWindow:
    name: str
    start: dt_time
    end: dt_time


def setup_logger(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("quant_7x24")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
    return logger


class Notifier:
    def __init__(self, config: Dict[str, Any], logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger

    def notify(self, subject: str, message: str) -> None:
        self._notify_slack(subject, message)
        self._notify_email(subject, message)

    def _notify_slack(self, subject: str, message: str) -> None:
        slack_cfg = self.config.get("notifications", {}).get("slack", {})
        if not slack_cfg.get("enabled"):
            return
        webhook = slack_cfg.get("webhook_url")
        if not webhook or requests is None:
            self.logger.warning("Slack enabled but requests/webhook missing.")
            return
        try:
            requests.post(webhook, json={"text": f"*{subject}*\n{message}"}, timeout=10)
        except Exception as exc:
            self.logger.warning("Slack notify failed: %s", exc)

    def _notify_email(self, subject: str, message: str) -> None:
        email_cfg = self.config.get("notifications", {}).get("email", {})
        if not email_cfg.get("enabled"):
            return
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = email_cfg["from_addr"]
            msg["To"] = email_cfg["to_addr"]
            msg.set_content(message)

            with smtplib.SMTP(email_cfg["smtp_host"], int(email_cfg.get("smtp_port", 587))) as smtp:
                smtp.starttls()
                smtp.login(email_cfg["username"], email_cfg["password"])
                smtp.send_message(msg)
        except Exception as exc:
            self.logger.warning("Email notify failed: %s", exc)


class RateLimiter:
    def __init__(self, calls_per_minute: int) -> None:
        self.min_interval = max(0.01, 60.0 / max(1, calls_per_minute))
        self._last_call = 0.0

    def wait(self) -> None:
        now = time.time()
        delta = now - self._last_call
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last_call = time.time()


class SQLiteStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self._init_tables()

    def _init_tables(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bars (
                symbol TEXT NOT NULL,
                ts TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                source TEXT,
                PRIMARY KEY (symbol, ts)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty REAL NOT NULL,
                price REAL NOT NULL,
                mode TEXT NOT NULL,
                reason TEXT
            )
            """
        )
        self.conn.commit()

    def upsert_bars(self, symbol: str, frame: pd.DataFrame, source: str) -> int:
        if frame.empty:
            return 0
        rows = []
        for _, row in frame.iterrows():
            ts = pd.to_datetime(row["Datetime"] if "Datetime" in row else row.name)
            rows.append(
                (
                    symbol,
                    ts.isoformat(),
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                    float(row.get("Volume", 0.0)),
                    source,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO bars (symbol, ts, open, high, low, close, volume, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, ts) DO UPDATE SET
              open=excluded.open,
              high=excluded.high,
              low=excluded.low,
              close=excluded.close,
              volume=excluded.volume,
              source=excluded.source
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_symbol_frame(self, symbol: str, limit: int = 5000) -> pd.DataFrame:
        q = """
            SELECT ts, open, high, low, close, volume
            FROM bars
            WHERE symbol = ?
            ORDER BY ts DESC
            LIMIT ?
        """
        df = pd.read_sql_query(q, self.conn, params=(symbol, limit))
        if df.empty:
            return df
        df["Date"] = pd.to_datetime(df["ts"])
        df = df.rename(
            columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
        )
        return df.sort_values("Date").reset_index(drop=True)[["Date", "Open", "High", "Low", "Close", "Volume"]]

    def record_trade(self, ts: datetime, symbol: str, side: str, qty: float, price: float, mode: str, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO trades (ts, symbol, side, qty, price, mode, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts.isoformat(), symbol, side, qty, price, mode, reason),
        )
        self.conn.commit()


if nn is not None:
    class SimpleLSTM(nn.Module):
        def __init__(self, input_dim: int, hidden_dim: int, num_layers: int, dropout: float) -> None:
            super().__init__()
            self.lstm = nn.LSTM(
                input_size=input_dim,
                hidden_size=hidden_dim,
                num_layers=num_layers,
                batch_first=True,
                dropout=dropout if num_layers > 1 else 0.0,
            )
            self.fc = nn.Linear(hidden_dim, 1)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            out, _ = self.lstm(x)
            return self.fc(out[:, -1, :])
else:
    class SimpleLSTM:  # type: ignore[override]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("PyTorch is not available. Install torch to enable training.")


class Quant7x24Engine:
    WINDOWS = (
        PhaseWindow("training", dt_time(9, 0), dt_time(16, 0)),
        PhaseWindow("preopen", dt_time(16, 0), dt_time(21, 30)),
        PhaseWindow("market_open", dt_time(21, 30), dt_time(4, 0)),
        PhaseWindow("post_market", dt_time(4, 0), dt_time(7, 0)),
    )

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.tz = ZoneInfo(config.get("timezone", "Asia/Taipei"))
        self.logger = setup_logger(Path(config["logging"]["file"]))
        self.notifier = Notifier(config, self.logger)
        self.rate_limiter = RateLimiter(config.get("api", {}).get("calls_per_minute", 60))
        self.store = SQLiteStore(Path(config["storage"]["sqlite_path"]))
        self.running = True
        self.device = self._resolve_device()
        self.model: Optional[nn.Module] = None
        self.last_phase = ""

        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)

    def _handle_stop(self, *_: Any) -> None:
        self.logger.info("Received stop signal, shutting down gracefully.")
        self.running = False

    def _resolve_device(self) -> str:
        if torch is None:
            return "cpu"
        if torch.cuda.is_available() and self.config.get("training", {}).get("prefer_cuda", True):
            return "cuda"
        return "cpu"

    def run_forever(self) -> None:
        poll_seconds = int(self.config.get("runtime", {}).get("poll_seconds", 60))
        self.logger.info("Starting 7x24 engine. timezone=%s device=%s", self.tz, self.device)

        while self.running:
            now = datetime.now(self.tz)
            phase = self._resolve_phase(now)
            if phase != self.last_phase:
                self.logger.info("Entering phase=%s", phase)
                self.last_phase = phase

            try:
                self._dispatch_phase(phase, now)
            except Exception as exc:
                self.logger.exception("Phase execution failed: %s", exc)
                self.notifier.notify("[quant_7x24] Phase error", f"phase={phase}\nerror={exc}")

            time.sleep(poll_seconds)

    def _resolve_phase(self, now: datetime) -> str:
        t = now.timetz().replace(tzinfo=None)
        for window in self.WINDOWS:
            if window.start <= window.end:
                if window.start <= t < window.end:
                    return window.name
            else:
                if t >= window.start or t < window.end:
                    return window.name
        return "maintenance"

    def _dispatch_phase(self, phase: str, now: datetime) -> None:
        if phase == "training":
            self.training_phase(now)
        elif phase == "preopen":
            self.preopen_phase(now)
        elif phase == "market_open":
            self.market_open_phase(now)
        elif phase == "post_market":
            self.post_market_phase(now)
        else:
            self.maintenance_phase(now)

    def _symbols(self) -> Sequence[str]:
        return self.config.get("universe", {}).get("symbols", ["AAPL", "MSFT", "NVDA"])

    def _fetch_intraday(self, symbol: str, period: str = "5d", interval: str = "1m") -> pd.DataFrame:
        self.rate_limiter.wait()
        if yf is None:
            self.logger.warning("yfinance not available, skip fetch for %s", symbol)
            return pd.DataFrame()
        df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False)
        if df.empty:
            return df
        df = df.rename_axis("Datetime").reset_index()
        return df[["Datetime", "Open", "High", "Low", "Close", "Volume"]]

    def collect_incremental_data(self, period: str = "5d", interval: str = "1m") -> None:
        for symbol in self._symbols():
            try:
                df = self._fetch_intraday(symbol, period=period, interval=interval)
                inserted = self.store.upsert_bars(symbol, df, source="yfinance")
                self.logger.info("Data upsert symbol=%s rows=%d", symbol, inserted)
            except Exception as exc:
                self.logger.warning("Data fetch failed symbol=%s err=%s", symbol, exc)

    def _build_dataset(self, symbol: str, lookback: int) -> Tuple[np.ndarray, np.ndarray]:
        df = self.store.get_symbol_frame(symbol, limit=10000)
        if len(df) <= lookback:
            return np.empty((0, lookback, 5), dtype=np.float32), np.empty((0, 1), dtype=np.float32)

        feats = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        mins = feats.min(axis=0)
        maxs = feats.max(axis=0)
        denom = (maxs - mins).replace(0, 1.0)
        scaled = ((feats - mins) / denom).clip(0, 1).to_numpy(dtype=np.float32)

        x, y = [], []
        close_idx = 3
        for i in range(lookback, len(scaled)):
            x.append(scaled[i - lookback : i])
            y.append([scaled[i, close_idx]])
        return np.asarray(x, dtype=np.float32), np.asarray(y, dtype=np.float32)

    def train_models(self) -> None:
        if torch is None or nn is None:
            self.logger.warning("Torch unavailable, skip training.")
            return

        cfg = self.config.get("training", {})
        lookback = int(cfg.get("lookback", 60))
        hidden_dim = int(cfg.get("hidden_dim", 64))
        num_layers = int(cfg.get("num_layers", 2))
        dropout = float(cfg.get("dropout", 0.2))
        lr = float(cfg.get("lr", 1e-3))
        batch_size = int(cfg.get("batch_size", 128))
        epochs = int(cfg.get("epochs", 3))

        x_parts, y_parts = [], []
        for symbol in self._symbols():
            x, y = self._build_dataset(symbol, lookback)
            if len(x):
                x_parts.append(x)
                y_parts.append(y)

        if not x_parts:
            self.logger.info("No training samples yet; skip training cycle.")
            return

        x_train = np.concatenate(x_parts, axis=0)
        y_train = np.concatenate(y_parts, axis=0)
        self.logger.info("Training samples=%d lookback=%d", len(x_train), lookback)

        dataset = TensorDataset(torch.tensor(x_train), torch.tensor(y_train))
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

        device = torch.device(self.device)
        model = SimpleLSTM(input_dim=5, hidden_dim=hidden_dim, num_layers=num_layers, dropout=dropout).to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=lr)
        criterion = nn.MSELoss()
        use_amp = device.type == "cuda"
        scaler = torch.amp.GradScaler("cuda", enabled=use_amp)

        model.train()
        for epoch in range(1, epochs + 1):
            losses = []
            for bx, by in loader:
                bx, by = bx.to(device), by.to(device)
                optimizer.zero_grad()
                with torch.autocast(device_type=device.type, enabled=use_amp):
                    pred = model(bx)
                    loss = criterion(pred, by)
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
                losses.append(float(loss.detach().cpu().item()))
            self.logger.info("Epoch %d/%d loss=%.6f", epoch, epochs, float(np.mean(losses)))

        self.model = model
        model_path = Path(self.config["training"].get("model_path", "artifacts/latest_model.pt"))
        model_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(model.state_dict(), model_path)
        self.logger.info("Model saved to %s", model_path)

    def preopen_phase(self, now: datetime) -> None:
        self.logger.info("[%s] pre-open prep", now.isoformat())
        self.collect_incremental_data(period="5d", interval="5m")
        self._run_news_placeholder()

    def training_phase(self, now: datetime) -> None:
        self.logger.info("[%s] training/data acquisition", now.isoformat())
        self.collect_incremental_data(period="30d", interval="5m")
        self.train_models()

    def _latest_close(self, symbol: str) -> Optional[float]:
        df = self.store.get_symbol_frame(symbol, limit=2)
        if df.empty:
            return None
        return float(df.iloc[-1]["Close"])

    def _generate_signal(self, symbol: str) -> Tuple[str, str]:
        df = self.store.get_symbol_frame(symbol, limit=200)
        if len(df) < 30:
            return "HOLD", "insufficient_data"
        close = df["Close"].astype(float)
        fast = close.rolling(10).mean().iloc[-1]
        slow = close.rolling(30).mean().iloc[-1]
        if np.isnan(fast) or np.isnan(slow):
            return "HOLD", "nan_ma"
        if fast > slow * 1.001:
            return "BUY", f"ma_fast({fast:.2f})>ma_slow({slow:.2f})"
        if fast < slow * 0.999:
            return "SELL", f"ma_fast({fast:.2f})<ma_slow({slow:.2f})"
        return "HOLD", "no_edge"

    def _execute_trade(self, now: datetime, symbol: str, side: str, reason: str) -> None:
        qty = float(self.config.get("trading", {}).get("qty", 1))
        dry_run = bool(self.config.get("trading", {}).get("dry_run", True))
        px = self._latest_close(symbol) or 0.0
        mode = "paper" if dry_run else "live"

        self.store.record_trade(now, symbol, side, qty, px, mode, reason)
        msg = f"{mode.upper()} {side} {symbol} qty={qty} px={px:.2f} reason={reason}"
        self.logger.info(msg)
        self.notifier.notify("[quant_7x24] trade", msg)

    def market_open_phase(self, now: datetime) -> None:
        self.logger.info("[%s] market-open realtime loop", now.isoformat())
        self.collect_incremental_data(period="1d", interval="1m")
        for symbol in self._symbols():
            signal_name, reason = self._generate_signal(symbol)
            if signal_name in {"BUY", "SELL"}:
                self._execute_trade(now, symbol, signal_name, reason)

    def post_market_phase(self, now: datetime) -> None:
        self.logger.info("[%s] post-market cleanup/report", now.isoformat())
        report_dir = Path(self.config.get("reporting", {}).get("dir", "reports/daily"))
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"report_{now.strftime('%Y%m%d')}.txt"
        with report_path.open("w", encoding="utf-8") as f:
            f.write(f"Generated at: {now.isoformat()}\n")
            f.write(f"Device: {self.device}\n")
            f.write(f"Symbols: {','.join(self._symbols())}\n")
        self.logger.info("Post-market report generated: %s", report_path)

    def maintenance_phase(self, now: datetime) -> None:
        self.logger.info("[%s] maintenance", now.isoformat())

    def _run_news_placeholder(self) -> None:
        # Hook for sentiment/news providers.
        self.logger.info("News/sentiment hook executed (placeholder).")


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="7x24 unattended US quant trading system")
    parser.add_argument("--config", default="scripts/quant_7x24_config.example.json")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        raise SystemExit(f"Config file not found: {cfg_path}")

    cfg = load_config(cfg_path)
    engine = Quant7x24Engine(cfg)
    engine.run_forever()


if __name__ == "__main__":
    main()
