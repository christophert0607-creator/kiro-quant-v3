import logging
import sys
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

from v3_pipeline.models.manager import ModelManager


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
class BacktestConfig:
    buy_threshold: float = 0.01
    sell_threshold: float = 0.01
    initial_cash: float = 100000.0
    commission: float = 0.001


class KiroStrategyFactory:
    """Factory to inject ModelManager and feature data into Backtrader strategy."""

    @staticmethod
    def build(model_manager: ModelManager, featured_df: pd.DataFrame, config: BacktestConfig):
        import backtrader as bt

        class KiroStrategy(bt.Strategy):
            params = dict(
                threshold_buy=config.buy_threshold,
                threshold_sell=config.sell_threshold,
            )

            def __init__(self):
                self.model_manager = model_manager
                self.featured_df = featured_df.reset_index(drop=True)
                self.logger = _build_stderr_logger("KiroStrategy")

            def next(self):
                idx = len(self) - 1
                if idx < 0 or idx >= len(self.featured_df):
                    return

                current_price = float(self.data.close[0])
                window_df = self.featured_df.iloc[: idx + 1]

                try:
                    predicted_price = self.model_manager.predict(window_df)
                except Exception as exc:
                    self.logger.warning("Prediction skipped at idx=%d: %s", idx, exc)
                    return

                buy_trigger = current_price * (1 + self.params.threshold_buy)
                sell_trigger = current_price * (1 - self.params.threshold_sell)

                if not self.position and predicted_price > buy_trigger:
                    self.buy()
                    self.logger.info(
                        "BUY idx=%d current=%.4f predicted=%.4f",
                        idx,
                        current_price,
                        predicted_price,
                    )
                elif self.position and predicted_price < sell_trigger:
                    self.sell()
                    self.logger.info(
                        "SELL idx=%d current=%.4f predicted=%.4f",
                        idx,
                        current_price,
                        predicted_price,
                    )

        return KiroStrategy


class BacktestEngine:
    def __init__(self, config: Optional[BacktestConfig] = None) -> None:
        self.config = config or BacktestConfig()
        self.logger = _build_stderr_logger(self.__class__.__name__)

    def run(self, featured_df: pd.DataFrame, model_manager: ModelManager) -> Dict[str, float]:
        import backtrader as bt

        df = featured_df.copy()
        if "Date" not in df.columns:
            raise ValueError("featured_df must contain 'Date'")

        bt_df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
        bt_df["Date"] = pd.to_datetime(bt_df["Date"], errors="coerce")
        bt_df = bt_df.dropna(subset=["Date"]).set_index("Date")

        cerebro = bt.Cerebro()
        cerebro.broker.setcash(self.config.initial_cash)
        cerebro.broker.setcommission(commission=self.config.commission)

        data_feed = bt.feeds.PandasData(dataname=bt_df)
        cerebro.adddata(data_feed)

        strategy_cls = KiroStrategyFactory.build(model_manager, df.reset_index(drop=True), self.config)
        cerebro.addstrategy(strategy_cls)

        cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")

        self.logger.info("Starting backtest with initial cash %.2f", self.config.initial_cash)
        results = cerebro.run()
        strat = results[0]

        returns_analysis = strat.analyzers.returns.get_analysis()
        sharpe_analysis = strat.analyzers.sharpe.get_analysis()
        drawdown_analysis = strat.analyzers.drawdown.get_analysis()

        total_return = float(returns_analysis.get("rtot", 0.0))
        sharpe_ratio = float(sharpe_analysis.get("sharperatio", 0.0) or 0.0)
        max_drawdown = float(drawdown_analysis.get("max", {}).get("drawdown", 0.0))

        summary = {
            "total_return": total_return,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": max_drawdown,
            "final_value": float(cerebro.broker.getvalue()),
        }
        self.logger.info("Backtest summary: %s", summary)
        return summary
