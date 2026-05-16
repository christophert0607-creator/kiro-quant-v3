import sqlite3
import pandas as pd
import os
import logging
from contextlib import closing
from typing import Optional, List

logger = logging.getLogger("DBManager")

class DatabaseManager:
    """Manages SQLite storage for market data and technical indicators."""

    def __init__(self, db_path: str = "kiro_quant.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize the database and base table."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("""
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
            """)
            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")

    def _ensure_columns(self, df: pd.DataFrame):
        """Ensure all columns in the DataFrame exist in the database table."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            cursor = conn.execute("PRAGMA table_info(market_data)")
            existing_cols = [row[1] for row in cursor.fetchall()]
            
            for col in df.columns:
                if col not in existing_cols:
                    # Sanitize column name and add as REAL
                    sanitized_col = "".join(c if c.isalnum() else "_" for c in col)
                    try:
                        conn.execute(f"ALTER TABLE market_data ADD COLUMN {sanitized_col} REAL")
                        logger.info(f"Added column {sanitized_col} to market_data table")
                    except sqlite3.OperationalError as e:
                        if "duplicate column name" not in str(e).lower():
                            logger.error(f"Error adding column {sanitized_col}: {e}")

    def save_data(self, df: pd.DataFrame, symbol: Optional[str] = None):
        """Save indicator-enriched DataFrame to SQLite using UPSERT logic."""
        if df.empty:
            return

        save_df = df.copy()
        if "symbol" not in save_df.columns and symbol:
            save_df["symbol"] = symbol
        
        if "Date" in save_df.columns:
            save_df = save_df.rename(columns={"Date": "timestamp"})
            
        # Basic column normalization
        save_df.columns = [c.lower() if c in ["Open", "High", "Low", "Close", "Volume"] else c for c in save_df.columns]
        
        # Ensure schema matches
        self._ensure_columns(save_df)
        
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            # We use to_sql with a temporary table for UPSERT-like behavior in SQLite < 3.24
            # or just 'REPLACE' if we trust the primary key
            save_df.to_sql("market_data", conn, if_exists="append", index=False, method=self._upsert_method)
            conn.commit()

    def _upsert_method(self, table, conn, keys, data_iter):
        """Custom insertion method for to_sql to support REPLACE."""
        from sqlite3 import IntegrityError
        
        columns = ', '.join(keys)
        placeholders = ', '.join(['?'] * len(keys))
        sql = f"INSERT OR REPLACE INTO {table.name} ({columns}) VALUES ({placeholders})"
        conn.executemany(sql, data_iter)

    def get_latest_data(self, symbol: str, limit: int = 100) -> pd.DataFrame:
        """Retrieve the latest entries for a symbol."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            query = f"SELECT * FROM market_data WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?"
            return pd.read_sql_query(query, conn, params=(symbol, limit))

    def check_health(self, symbols: List[str]) -> dict:
        """Check the data range for each symbol and return a report of missing chunks."""
        report = {}
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            for sym in symbols:
                cursor = conn.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM market_data WHERE symbol = ?", (sym,))
                min_t, max_t, count = cursor.fetchone()
                report[sym] = {
                    "start": min_t,
                    "end": max_t,
                    "count": count
                }
        return report
