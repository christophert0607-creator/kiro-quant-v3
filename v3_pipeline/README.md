# Kiro Quant V3.0 Pipeline Structure

## 📂 Directory Layout
- **core/**: trading loop, broker connectors, strategy and priming modules
- **data/**: acquisition + preprocessing
- **features/**: technical indicators
- **models/**: model management and inference
- **risk/**: risk controls
- **logs/**: runtime logs, archive parquet

## 🔄 Current Pipeline Logic
1. Prime history for all symbols
2. Fetch live quotes (multi-provider fallback)
3. Normalize/merge buffers + data-source tagging
4. Generate indicators and model predictions
5. Risk-gated execution and notifications
6. Archive data/profile timings to parquet

## 🚀 V3 Deep Optimization (20 Core Items)
1. **Data source tagging**: every bar tracks `data_source` (`FUTU/YF_LIVE/EF_LIVE/CACHED`).
2. **yfinance proxy matrix**: randomized User-Agent + proxy pool (`YF_PROXY_POOL`).
3. **Hard cache discipline**: elite symbols invalidate cache after 3 consecutive cached reads.
4. **Parallel quote pool**: asyncio gather with configurable concurrency (up to 111 symbols).
5. **WebSocket hardening**: planned for `kiro_bridge.py` heartbeat + reconnect hooks.
6. **LSTM rolling normalization**: planned DataPreparer upgrade interface.
7. **Multi-task training**: strategy/model orchestration scaffold added for symbol-wise expansion.
8. **Instant full-blood priming**: startup parallel priming for all configured symbols (default 5d 1m).
9. **Feature importance analyzer**: strategy layer reserved for indicator contribution pipeline.
10. **Dynamic model switch**: VIX-aware profile switching (`normal/cautious/defensive`).
11. **Zero-latency sim fill**: paper execution uses calibrated fill/slippage path.
12. **Smart capital allocation**: confidence score maps to 1%~10% risk budget.
13. **Trailing stop 2.0**: time-decay + volatility-tracking stop logic.
14. **Dual-channel trading lock**: planned for production API secondary confirmation.
15. **Cost erosion simulation**: commission model in strategy factory (`$1.99/trade`).
16. **Tactical heatmap API**: planned RSI/volume distribution endpoint for 111 symbols.
17. **Abnormal behavior alerts**: `[CRIT]` alerts + Telegram trigger for violent short-term move.
18. **Persistence compression**: bars and profiling persisted to Snappy Parquet archives.
19. **Performance profiling**: per-symbol end-to-end millisecond timing logs.
20. **Holographic daily snapshot**: planned EOD fleet PnL visual report pipeline.

---
## V4.0: Galactic Conquest

- **核心科技**：引入 Reinforcement Learning (RL) 強化學習，讓系統自我對弈、自我優化。
- **情報體系**：結合 LLM 進行 Sentiment Engine（情緒引擎），監控 Reddit / X 市場風向。
- **通訊協議**：支援多經紀商（Multi-Broker）混合下單與多雲分佈式主機部署。
- **人機合一**：全語音指令操控及超低延遲手機端 Dashboard。


## V4.0 Phase 1-2 Execution Snapshot
- **Kiro-Alpha-Engine**: added walk-forward factor selection (`core/alpha_engine.py`) to score and keep top factors per symbol.
- **Monte Carlo Simulator**: added 1000-sim pre-trade stress test (`core/monte_carlo.py`) with VaR/CVaR/win-rate outputs.
- **Risk of Ruin (ROR)**: integrated ruin-risk gate in `risk/manager.py` and invoked in live entry decisions (`core/main_loop.py`).
- **WFA + ROR Integration**: live loop now performs WFA feature selection before prediction and blocks BUY actions when ROR/VaR constraints are violated.

### V4.0 Strategic Blueprint (Formal)
- **RL 強化學習核心**：導入策略自我博弈與在線獎懲更新機制。
- **LLM 情緒引擎**：融合 Reddit/X 與新聞流的情緒向量，作為 Alpha 先驗。
- **多雲與多券商佈署**：以 broker abstraction + cloud failover 進行低中斷交易編排。
