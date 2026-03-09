import logging
import sys
from typing import List

import numpy as np
import pandas as pd

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


class TechnicalIndicatorGenerator:
    """Feature module for generating technical indicators from OHLCV DataFrames."""

    def __init__(self):
        self.logger = _build_stderr_logger(self.__class__.__name__)
        try:
            import talib as ta  # type: ignore

            self.ta = ta
            self.use_talib = True
            self.logger.info("TA-Lib backend enabled")
        except Exception as exc:  # fallback path for environments without TA-Lib
            self.ta = None
            self.use_talib = False
            self.logger.warning("TA-Lib unavailable, using pandas fallback indicators: %s", exc)

    def generate(self, data: pd.DataFrame) -> pd.DataFrame:
        self._validate_input(data)

        df = data.copy()
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.sort_values("Date").reset_index(drop=True)

        if self.use_talib:
            featured = self._with_talib(df)
        else:
            featured = self._fallback(df)

        generated_columns = [c for c in featured.columns if c not in REQUIRED_COLUMNS]
        self.logger.info("Generated %d indicators", len(generated_columns))
        return featured

    def _validate_input(self, data: pd.DataFrame) -> None:
        missing = [col for col in REQUIRED_COLUMNS if col not in data.columns]
        if missing:
            msg = f"Input data missing required columns: {missing}"
            self.logger.error(msg)
            raise ValueError(msg)

    def _with_talib(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["Close"].astype(float).values
        high = df["High"].astype(float).values
        low = df["Low"].astype(float).values
        volume = df["Volume"].astype(float).values

        df["SMA_5"] = self.ta.SMA(close, timeperiod=5)
        df["SMA_10"] = self.ta.SMA(close, timeperiod=10)
        df["SMA_20"] = self.ta.SMA(close, timeperiod=20)
        df["EMA_12"] = self.ta.EMA(close, timeperiod=12)
        df["EMA_26"] = self.ta.EMA(close, timeperiod=26)
        df["RSI_14"] = self.ta.RSI(close, timeperiod=14)

        macd, macd_signal, macd_hist = self.ta.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
        df["MACD"] = macd
        df["MACD_SIGNAL"] = macd_signal
        df["MACD_HIST"] = macd_hist

        upper, middle, lower = self.ta.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)
        df["BB_UPPER"] = upper
        df["BB_MIDDLE"] = middle
        df["BB_LOWER"] = lower

        df["ATR_14"] = self.ta.ATR(high, low, close, timeperiod=14)
        df["ADX_14"] = self.ta.ADX(high, low, close, timeperiod=14)
        df["CCI_14"] = self.ta.CCI(high, low, close, timeperiod=14)
        df["MFI_14"] = self.ta.MFI(high, low, close, volume, timeperiod=14)
        df["OBV"] = self.ta.OBV(close, volume)
        df["ROC_10"] = self.ta.ROC(close, timeperiod=10)
        df["WILLR_14"] = self.ta.WILLR(high, low, close, timeperiod=14)
        return df

    def _fallback(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["Close"].astype(float)
        high = df["High"].astype(float)
        low = df["Low"].astype(float)
        volume = df["Volume"].astype(float)

        df["SMA_5"] = close.rolling(5).mean()
        df["SMA_10"] = close.rolling(10).mean()
        df["SMA_20"] = close.rolling(20).mean()
        df["EMA_12"] = close.ewm(span=12, adjust=False).mean()
        df["EMA_26"] = close.ewm(span=26, adjust=False).mean()

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df["RSI_14"] = 100 - (100 / (1 + rs))

        macd = df["EMA_12"] - df["EMA_26"]
        signal = macd.ewm(span=9, adjust=False).mean()
        df["MACD"] = macd
        df["MACD_SIGNAL"] = signal
        df["MACD_HIST"] = macd - signal

        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        df["BB_UPPER"] = mid + 2 * std
        df["BB_MIDDLE"] = mid
        df["BB_LOWER"] = mid - 2 * std

        tr_components = pd.concat(
            [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        )
        tr = tr_components.max(axis=1)
        df["ATR_14"] = tr.rolling(14).mean()

        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr14 = tr.rolling(14).sum()
        plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(14).sum() / tr14
        minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(14).sum() / tr14
        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di)).replace([np.inf, -np.inf], np.nan)
        df["ADX_14"] = dx.rolling(14).mean()

        typical = (high + low + close) / 3
        cci_den = 0.015 * (typical - typical.rolling(14).mean()).abs().rolling(14).mean()
        df["CCI_14"] = (typical - typical.rolling(14).mean()) / cci_den.replace(0, np.nan)

        money_flow = typical * volume
        positive_flow = money_flow.where(typical > typical.shift(), 0.0).rolling(14).sum()
        negative_flow = money_flow.where(typical < typical.shift(), 0.0).rolling(14).sum()
        mfr = positive_flow / negative_flow.replace(0, np.nan)
        df["MFI_14"] = 100 - (100 / (1 + mfr))

        direction = np.sign(close.diff()).fillna(0.0)
        df["OBV"] = (direction * volume).cumsum()
        df["ROC_10"] = close.pct_change(10) * 100
        highest = high.rolling(14).max()
        lowest = low.rolling(14).min()
        df["WILLR_14"] = -100 * (highest - close) / (highest - lowest).replace(0, np.nan)
        return df

    def indicator_columns(self, data: pd.DataFrame) -> List[str]:
        return [col for col in data.columns if col not in REQUIRED_COLUMNS]
