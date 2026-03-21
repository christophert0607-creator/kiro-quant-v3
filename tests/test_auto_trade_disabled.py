import asyncio
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.modules.setdefault("requests", SimpleNamespace(post=lambda *args, **kwargs: None))


@dataclass
class _ImportStubPreparer:
    lookback: int = 2
    target_col: str = "Close"
    is_fitted: bool = True
    feature_columns: list[str] | None = None

    def fit_transform(self, frame):
        self.is_fitted = True
        self.feature_columns = [c for c in frame.columns if c != "Date"]
        return frame


sys.modules.setdefault(
    "v3_pipeline.models.manager",
    SimpleNamespace(DataPreparer=lambda **kwargs: _ImportStubPreparer(**kwargs), ModelManager=object),
)

from v3_pipeline.core.main_loop import LiveConfig, LiveTradingLoop
from tests.test_main_loop_state_restore import _DummyModelManager, _DummyRiskController, _DummyConnector, _DummyDataManager


def test_execute_skips_when_auto_trade_disabled(tmp_path: Path):
    db_path = tmp_path / "kiro_quant.db"
    cfg = LiveConfig(symbol="TSLA", symbols_list=["TSLA"], paper_trading=True, auto_trade=False)
    loop = LiveTradingLoop(
        model_manager=_DummyModelManager(),
        risk_controller=_DummyRiskController(),
        futu_connector=_DummyConnector(),
        data_manager=_DummyDataManager(),
        config=cfg,
    )
    loop.db.db_path = str(db_path)
    loop.db._init_db()
    loop.latest_prices = {"TSLA": 100.0}
    loop.health.path = tmp_path / "health.json"

    asyncio.run(loop._execute("TSLA", "BUY", 5, 100.0, "unit_test"))

    assert loop.position_qty_by_symbol["TSLA"] == 0
    assert loop.pnl_tracker.fill_count == 0
    assert loop.paper_trading_simulator.tracker.fill_count == 0

    with sqlite3.connect(db_path) as conn:
        exec_count = conn.execute("select count(*) from executions").fetchone()[0]
        risk_count = conn.execute("select count(*) from risk_events where event_code = 'AUTO_TRADE_DISABLED'").fetchone()[0]

    assert exec_count == 0
    assert risk_count == 1
