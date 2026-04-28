# Memory Log - 2026-04-15

## Quant Engine Restoration (HK Afternoon Session)

Today I performed a comprehensive restoration of the Kiro V3 Quant Engine after identifying multiple critical failures.

### Fixed Issues:
1. **Model Architecture Mismatch**: 
   - Identified that `v3_hk_stocks.pth` uses `AttentiveKiroLSTM` but the loader was hardcoded for `KiroLSTM`.
   - Patched `v3_pipeline/models/manager.py` to auto-detect and instantiate the correct class.
2. **FutuOpenD Port Configuration**:
   - Fixed `FutuConnector` logic to correctly load the `11112` port from `config.json`. Previously it was defaulting to `11111`, causing timeouts.
3. **Missing Launcher Functions**:
   - Restored `_idle_precompute`, `_collect_only_cycle`, and `_enable_dry_run_log_prefix` in `v3_launcher.py` which were accidentally deleted during file edits.
4. **Futu API Subscription**:
   - Implemented `subscribe_symbols` in `FutuConnector` to resolve the "Subscribe to Basic data first" error when fetching real-time quotes.
   - Updated `v3_launcher.py` to trigger subscriptions on start and market switch.
5. **Output Buffering**:
   - Switched to `python3 -u` (unbuffered) for the launcher to ensure real-time log visibility.

### Current Status:
- **Engine**: PID 155459 running stably.
- **Data Source**: Real-time 1-minute bars from `source=FUTU` confirmed.
- **Market**: HK Afternoon session active.
- **US Prep**: Background precompute for 59 US symbols active.

- **HK Afternoon Results**: Successfully triggered first batch of simulated trades at 14:15 HKT.
  - **Trades**: 0960.HK (Long 200), 0939.HK (Long 200), 0688.HK (Short 100).
  - **Validation**: Confirmed lot size rounding and sentiment gate fixes are operational.

### Decisions:
- Prioritize `futu` provider for HK symbols to ensure intraday data accuracy.
- Keep the engine running in background `nohup` mode for session persistence.
- Unlock HK trading via lot size and sentiment threshold adjustments.
