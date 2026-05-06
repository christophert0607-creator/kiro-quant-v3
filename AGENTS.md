## graphify

This project has a graphify knowledge graph at graphify-out/.

### Knowledge Graph Snapshot (2026-05-06)
- **Status**: HEALTHY (Deep Rebuilt)
- **Stats**: 1811 nodes · 3062 edges · 109 communities
- **God Nodes**: 1. `FutuConnector` - 64 edges,2. `LiveTradingLoop` - 63 edges,3. `TechnicalIndicatorGenerator` - 53 edges,4. `ModelManager` - 41 edges,5. `DataPreparer` - 39 edges,

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- **IMPORTANT**: If new code is not showing up, run `bash scripts/rebuild_graph.sh` to force a deep refresh.

## Rewrite Progress (CLAUDE_CODE_REWRITE_GUIDE.md)

- [x] Phase 1 — Tests for `resolve_symbol()`, `_execute()` modes, `max_positions`, checkpoint metadata
- [x] Phase 2 — `ConfigManager` with typed schema (`v3_pipeline/config/manager.py` + `tests/test_config_manager.py`, 2026-05-07)
- [ ] Phase 3 — `MarketContext` isolation
- [ ] Phase 4 — Execution state machine
- [ ] Phase 5 — `ModelRegistry`
- [ ] Phase 6 — Data/observability refactor
- [ ] Phase 7 — Idle-time optimization

### Phase 2 summary (2026-05-07)
Created `v3_pipeline/config/` package with:
- `manager.py`: `AppConfig`, `FutuCfg`, `V3LiveCfg`, `ModelCfg`, `CapitalBucketsCfg` dataclasses
- `ConfigManager` class: loads `config.json`, applies env overrides (env > json > default), validates and logs
- `load_config()` module-level singleton helper
- `tests/test_config_manager.py`: 30 unit tests covering load, defaults, env overrides, validation, reload, raw access
- Env override map: `FUTU_OPEND_HOST`, `FUTU_OPEND_PORT`, `FUTU_TRD_ENV`, `FUTU_TARGET_ACC_ID`, `V3_AUTO_TRADE`, `V3_PAPER_TRADING`, `V3_MAX_POSITIONS`, `V3_POLLING_SECONDS`
- Backward compatible with current `config.json` structure (no keys changed)
