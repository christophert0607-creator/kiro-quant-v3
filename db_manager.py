import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional, List

import pandas as pd

logger = logging.getLogger("DBManager")


class DatabaseManager:
    """Manages SQLite storage for market data plus trading/audit records."""

    def __init__(self, db_path: str = "kiro_quant.db"):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        """Initialize database tables used by the trading system."""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_data (
                    symbol TEXT,
                    timestamp DATETIME,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    PRIMARY KEY (symbol, timestamp)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    requested_price REAL,
                    fill_price REAL,
                    reason TEXT,
                    mode TEXT,
                    order_status TEXT,
                    metadata_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS position_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    avg_cost REAL NOT NULL,
                    source TEXT,
                    reason TEXT,
                    metadata_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pnl_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    cash REAL,
                    equity REAL,
                    realized_pnl REAL,
                    unrealized_pnl REAL,
                    net_pnl REAL,
                    fill_count INTEGER,
                    metadata_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS risk_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT,
                    side TEXT,
                    event_code TEXT NOT NULL,
                    severity TEXT,
                    reason TEXT,
                    message TEXT,
                    metadata_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    channel TEXT,
                    message TEXT NOT NULL,
                    metadata_json TEXT
                )
                """
            )
            conn.commit()
            logger.info("Database initialized at %s", self.db_path)

    def _ensure_columns(self, df: pd.DataFrame):
        """Ensure all columns in the DataFrame exist in the database table."""
        with self._connect() as conn:
            cursor = conn.execute("PRAGMA table_info(market_data)")
            existing_cols = [row[1] for row in cursor.fetchall()]

            for col in df.columns:
                if col not in existing_cols:
                    sanitized_col = "".join(c if c.isalnum() else "_" for c in col)
                    col_type = "TEXT" if sanitized_col.lower() in {"symbol", "timestamp", "source", "data_source"} else "REAL"
                    try:
                        conn.execute(f"ALTER TABLE market_data ADD COLUMN {sanitized_col} {col_type}")
                        logger.info("Added column %s to market_data table", sanitized_col)
                    except sqlite3.OperationalError as e:
                        if "duplicate column name" not in str(e).lower():
                            logger.error("Error adding column %s: %s", sanitized_col, e)

    def _json(self, payload: Optional[dict[str, Any]]) -> str:
        return json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def save_data(self, df: pd.DataFrame, symbol: Optional[str] = None):
        """Save indicator-enriched DataFrame to SQLite using UPSERT logic."""
        if df.empty:
            return

        save_df = df.copy()
        if "symbol" not in save_df.columns and symbol:
            save_df["symbol"] = symbol

        if "Date" in save_df.columns:
            save_df = save_df.rename(columns={"Date": "timestamp"})

        save_df.columns = [c.lower() if c in ["Open", "High", "Low", "Close", "Volume"] else c for c in save_df.columns]
        self._ensure_columns(save_df)

        with self._connect() as conn:
            save_df.to_sql("market_data", conn, if_exists="append", index=False, method=self._upsert_method)
            conn.commit()

    def save_market_quote(self, symbol: str, quote: dict[str, Any]) -> None:
        """Persist one fetched quote/OHLCV snapshot to market_data.

        This is intentionally tolerant: missing OHLC values fall back to the
        available price/close so real-time quote snapshots can share the same
        table as historical bars.
        """
        if not symbol or not quote:
            return

        close = quote.get("Close", quote.get("close", quote.get("price")))
        if close is None:
            return

        timestamp = quote.get("Date", quote.get("timestamp", self._now()))
        row = {
            "symbol": symbol,
            "Date": timestamp,
            "Open": quote.get("Open", quote.get("open", close)),
            "High": quote.get("High", quote.get("high", close)),
            "Low": quote.get("Low", quote.get("low", close)),
            "Close": close,
            "Volume": quote.get("Volume", quote.get("volume", 0.0)),
        }
        source = quote.get("data_source", quote.get("source"))
        if source is not None:
            row["source"] = source

        self.save_data(pd.DataFrame([row]), symbol=symbol)

    def _upsert_method(self, table, conn, keys, data_iter):
        columns = ", ".join(keys)
        placeholders = ", ".join(["?"] * len(keys))
        sql = f"INSERT OR REPLACE INTO {table.name} ({columns}) VALUES ({placeholders})"
        conn.executemany(sql, data_iter)

    def save_execution(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        requested_price: float,
        fill_price: float,
        reason: str,
        mode: str,
        order_status: str = "filled",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO executions (
                    ts, symbol, side, qty, requested_price, fill_price, reason, mode, order_status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(),
                    symbol,
                    side,
                    int(qty),
                    float(requested_price),
                    float(fill_price),
                    reason,
                    mode,
                    order_status,
                    self._json(metadata),
                ),
            )
            conn.commit()

    def save_position_snapshot(
        self,
        *,
        symbol: str,
        qty: int,
        avg_cost: float,
        source: str,
        reason: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO position_snapshots (ts, symbol, qty, avg_cost, source, reason, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (self._now(), symbol, int(qty), float(avg_cost), source, reason, self._json(metadata)),
            )
            conn.commit()

    def save_pnl_snapshot(
        self,
        *,
        cash: Optional[float],
        equity: Optional[float],
        realized_pnl: Optional[float],
        unrealized_pnl: Optional[float],
        net_pnl: Optional[float],
        fill_count: Optional[int],
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pnl_snapshots (
                    ts, cash, equity, realized_pnl, unrealized_pnl, net_pnl, fill_count, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(),
                    float(cash) if cash is not None else None,
                    float(equity) if equity is not None else None,
                    float(realized_pnl) if realized_pnl is not None else None,
                    float(unrealized_pnl) if unrealized_pnl is not None else None,
                    float(net_pnl) if net_pnl is not None else None,
                    int(fill_count) if fill_count is not None else None,
                    self._json(metadata),
                ),
            )
            conn.commit()

    def save_risk_event(
        self,
        *,
        event_code: str,
        message: str,
        severity: str = "INFO",
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO risk_events (ts, symbol, side, event_code, severity, reason, message, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (self._now(), symbol, side, event_code, severity, reason, message, self._json(metadata)),
            )
            conn.commit()

    def save_alert(self, *, channel: str, message: str, metadata: Optional[dict[str, Any]] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alerts (ts, channel, message, metadata_json)
                VALUES (?, ?, ?, ?)
                """,
                (self._now(), channel, message, self._json(metadata)),
            )
            conn.commit()

    def get_latest_data(self, symbol: str, limit: int = 100) -> pd.DataFrame:
        with self._connect() as conn:
            query = "SELECT * FROM market_data WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?"
            return pd.read_sql_query(query, conn, params=(symbol, limit))

    def check_health(self, symbols: List[str]) -> dict:
        report = {}
        with self._connect() as conn:
            for sym in symbols:
                cursor = conn.execute(
                    "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM market_data WHERE symbol = ?",
                    (sym,),
                )
                min_t, max_t, count = cursor.fetchone()
                report[sym] = {"start": min_t, "end": max_t, "count": count}
        return report
