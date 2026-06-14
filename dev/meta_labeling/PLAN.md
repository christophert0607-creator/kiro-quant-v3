# Meta-labeling Dev Loop Plan

Safety scope: only low-risk, reversible dev/test/reporting changes. Do not modify live trading or risk logic without explicit user approval.

## Current phase: shadow telemetry and promotion safety

- meta_056: Add read-only gate shadow audit script for `trade_quality_gate` / `meta_label_gate` events in `logs/decisions.jsonl`.
- meta_057: Add regression fixture/test for the gate shadow audit parser so future log-shape changes are caught without touching runtime.
- meta_058: Add compact daily health report wrapper combining prediction health + gate shadow audit + provenance eligibility.
- meta_059: Review whether any true broker/paper provenance rows exist; keep meta gate in shadow/no-data until evidence is durable.
- meta_060: Add a small regression fixture/test for the provenance rows review eligibility classification (synthetic vs paper/live broker evidence) without touching runtime.
- meta_061: Add a read-only shadow/provenance safety check that combines gate telemetry with immutable provenance review and returns a conservative enforcement verdict.
- meta_062: Integrate the safety-check summary into the compact daily health report wrapper, keeping it read-only and runtime-neutral.
- meta_063: Add a small CLI-friendly compact text/JSON summary mode for the daily health safety verdict, without changing enforcement/runtime logic.
- meta_064: Add a small operator-facing example/snippet or smoke-test wrapper for consuming the compact safety summary in cron/reporting, keeping it read-only.
- meta_065: Add one more regression guard for cron consumer alert classification when `live_trading_changes` is unexpectedly not false, keeping it read-only.
- meta_066: Add a read-only regression fixture for missing/non-boolean `live_trading_changes` payload handling in cron/reporting summaries.
- meta_067: Add a read-only regression fixture for malformed/non-object compact safety payload handling in cron/reporting summaries.
- meta_068: Add a read-only regression fixture for compact report subprocess failure handling in cron/reporting summaries.
- meta_069: Add a read-only regression fixture for compact report launch/missing-script failure handling in cron/reporting summaries.
- meta_070: Add a read-only regression fixture for compact report timeout handling in cron/reporting summaries.
