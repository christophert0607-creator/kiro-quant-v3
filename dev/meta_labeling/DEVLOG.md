# Meta-labeling Devlog

## 2026-06-11 12:02:00 CST — meta_069 report launch/missing-script failure fixture

- Hardened `self_learn/scripts/meta_064_cron_safety_summary_smoke.py` so missing compact report scripts and Python/report launch failures are converted into a conservative operator-visible summary.
- New launch-failure output emits `alert=critical_compact_report_launch_failed`, keeps `enforcement_safe=false`, recommends keeping enforcement disabled, includes `compact_safety_payload_malformed:report_launch_failed:*`, and formats `live_trading_changes=unexpected`.
- Extended `tests/test_meta_064_cron_safety_summary_smoke.py` with read-only fixtures for missing report script and missing Python executable; strict smoke mode returns exit code `2` for these critical conditions.
- Updated `dev/meta_labeling/PLAN.md` with `meta_070` as the next small read-only timeout handling guard fixture step.
- Observed live read-only cron consumer result remains expected shadow-blocked: `alert=expected_shadow_blocked`, `enforcement_safe=false`, `real_source_verified=false`, eligible real outcomes `0/100`; blockers were `insufficient_eligible_real_outcomes:0/100` and `meta_label_source_not_ok_events:1922`.
- Verification:
  - `python3 -m py_compile self_learn/scripts/meta_064_cron_safety_summary_smoke.py tests/test_meta_064_cron_safety_summary_smoke.py`
  - `python3 -m pytest -q tests/test_meta_064_cron_safety_summary_smoke.py` → `11 passed in 0.19s`
  - `python3 -m pytest -q tests/test_meta_058_daily_health_report.py tests/test_meta_064_cron_safety_summary_smoke.py` → `14 passed in 0.25s`
  - `python3 self_learn/scripts/meta_064_cron_safety_summary_smoke.py --days 1 --min-eligible-outcomes 100` → one-line `META_LABEL_CRON_CONSUMER ... live_trading_changes=false`
- Live trading/risk logic changed: **No**.
- Next: `meta_070_cron_consumer_report_timeout_fixture` — add a read-only fixture for compact report timeout handling.

## 2026-06-11 11:02:10 CST — meta_068 report subprocess failure fixture

- Hardened `self_learn/scripts/meta_064_cron_safety_summary_smoke.py` so a failing compact safety report subprocess is converted into a conservative operator-visible summary instead of crashing without a `META_LABEL_CRON_CONSUMER` line.
- New failure output emits `alert=critical_compact_report_failed`, keeps `enforcement_safe=false`, recommends keeping enforcement disabled, includes `compact_safety_payload_malformed:report_subprocess_failed:exit_<code>`, and formats `live_trading_changes=unexpected`.
- Extended `tests/test_meta_064_cron_safety_summary_smoke.py` with a read-only fake report fixture that exits non-zero; strict smoke mode returns exit code `2` for this critical condition.
- Updated `dev/meta_labeling/PLAN.md` with `meta_069` as the next small read-only launch/missing-script failure guard fixture step.
- Observed live read-only cron consumer result remains expected shadow-blocked: `alert=expected_shadow_blocked`, `enforcement_safe=false`, `real_source_verified=false`, eligible real outcomes `0/100`; blockers were `insufficient_eligible_real_outcomes:0/100` and `meta_label_source_not_ok_events:1745`.
- Verification:
  - `python3 -m py_compile self_learn/scripts/meta_064_cron_safety_summary_smoke.py tests/test_meta_064_cron_safety_summary_smoke.py`
  - `python3 -m pytest -q tests/test_meta_064_cron_safety_summary_smoke.py` → `9 passed in 0.17s`
  - `python3 -m pytest -q tests/test_meta_058_daily_health_report.py tests/test_meta_064_cron_safety_summary_smoke.py` → `12 passed in 0.28s`
  - `python3 self_learn/scripts/meta_064_cron_safety_summary_smoke.py --days 1 --min-eligible-outcomes 100` → one-line `META_LABEL_CRON_CONSUMER ... live_trading_changes=false`
- Live trading/risk logic changed: **No**.
- Next: `meta_069_cron_consumer_report_launch_failure_fixture` — add a read-only fixture for report launch/missing-script failure handling.

## 2026-06-11 10:01:11 CST — meta_067 malformed compact payload fixtures

- Hardened `self_learn/scripts/meta_064_cron_safety_summary_smoke.py` so malformed compact safety JSON and non-object JSON payloads are converted into a conservative synthetic summary instead of crashing without an operator line.
- The malformed summary emits `alert=critical_compact_payload_malformed`, keeps `enforcement_safe=false`, recommends keeping enforcement disabled, and formats `live_trading_changes=unexpected` for visibility.
- Extended `tests/test_meta_064_cron_safety_summary_smoke.py` with read-only regression fixtures for non-object compact JSON and invalid JSON output; strict smoke mode returns exit code `2` for both cases.
- Updated `dev/meta_labeling/PLAN.md` with `meta_068` as the next small read-only guard fixture step.
- Observed live read-only cron consumer result remains expected shadow-blocked: `alert=expected_shadow_blocked`, `enforcement_safe=false`, `real_source_verified=false`, eligible real outcomes `0/100`; blockers were `insufficient_eligible_real_outcomes:0/100` and `meta_label_source_not_ok_events:1620`.
- Verification:
  - `python3 -m py_compile self_learn/scripts/meta_064_cron_safety_summary_smoke.py tests/test_meta_064_cron_safety_summary_smoke.py`
  - `python3 -m pytest -q tests/test_meta_064_cron_safety_summary_smoke.py` → `8 passed in 0.16s`
  - `python3 -m pytest -q tests/test_meta_058_daily_health_report.py tests/test_meta_064_cron_safety_summary_smoke.py` → `11 passed in 0.24s`
  - `python3 self_learn/scripts/meta_064_cron_safety_summary_smoke.py --days 1 --min-eligible-outcomes 100` → one-line `META_LABEL_CRON_CONSUMER ... live_trading_changes=false`
- Live trading/risk logic changed: **No**.
- Next: `meta_068_cron_consumer_report_subprocess_failure_fixture` — add a read-only fixture for compact report subprocess failure handling.

## 2026-06-11 09:01:29 CST — meta_066 missing/non-boolean live-trading flag fixtures

- Extended `tests/test_meta_064_cron_safety_summary_smoke.py` with read-only regression fixtures for compact safety payloads where `live_trading_changes` is missing or a non-boolean string.
- Both fixtures prove the cron/operator consumer emits `alert=critical_live_trading_flag_unexpected`, formats the flag as `live_trading_changes=unexpected`, and returns strict smoke exit code `2` without changing runtime behavior.
- Updated `dev/meta_labeling/PLAN.md` with `meta_067` as the next small read-only guard fixture step.
- Observed live read-only cron consumer result remains expected shadow-blocked: `alert=expected_shadow_blocked`, `enforcement_safe=false`, `real_source_verified=false`, eligible real outcomes `0/100`; blockers were `insufficient_eligible_real_outcomes:0/100` and `meta_label_source_not_ok_events:1486`.
- Verification:
  - `python3 -m py_compile tests/test_meta_064_cron_safety_summary_smoke.py self_learn/scripts/meta_064_cron_safety_summary_smoke.py`
  - `python3 -m pytest -q tests/test_meta_064_cron_safety_summary_smoke.py` → `6 passed in 0.12s`
  - `python3 -m pytest -q tests/test_meta_058_daily_health_report.py tests/test_meta_064_cron_safety_summary_smoke.py` → `9 passed in 0.19s`
  - `python3 self_learn/scripts/meta_064_cron_safety_summary_smoke.py --days 1 --min-eligible-outcomes 100` → one-line `META_LABEL_CRON_CONSUMER ... live_trading_changes=false`
- Live trading/risk logic changed: **No**.
- Next: `meta_067_cron_consumer_malformed_non_object_payload_fixture` — add a read-only fixture for malformed/non-object compact safety payload handling.

## 2026-06-08 12:01:36 CST — meta_065 cron live-trading flag guard fixture

- Hardened `self_learn/scripts/meta_064_cron_safety_summary_smoke.py` so cron/operator output no longer hardcodes `live_trading_changes=false`; unexpected payload values now classify as `critical_live_trading_flag_unexpected` and show the actual `live_trading_changes=true`/`unexpected` token.
- Extended `tests/test_meta_064_cron_safety_summary_smoke.py` with a read-only regression fixture proving an unexpected `live_trading_changes=True` compact payload triggers critical alert formatting and strict smoke exit code `2`.
- Updated `dev/meta_labeling/PLAN.md` with `meta_066` as the next small read-only guard fixture step.
- Observed live read-only cron consumer result remains expected shadow-blocked: `alert=expected_shadow_blocked`, `enforcement_safe=false`, `real_source_verified=false`, eligible real outcomes `0/100`; blockers were `insufficient_eligible_real_outcomes:0/100` and `meta_label_source_not_ok_events:2778`.
- Verification:
  - `python3 -m py_compile self_learn/scripts/meta_064_cron_safety_summary_smoke.py tests/test_meta_064_cron_safety_summary_smoke.py`
  - `python3 -m pytest -q tests/test_meta_064_cron_safety_summary_smoke.py` → `4 passed in 0.08s`
  - `python3 -m pytest -q tests/test_meta_058_daily_health_report.py tests/test_meta_064_cron_safety_summary_smoke.py` → `7 passed in 0.16s`
  - `python3 self_learn/scripts/meta_064_cron_safety_summary_smoke.py --days 1 --min-eligible-outcomes 100` → one-line `META_LABEL_CRON_CONSUMER ... live_trading_changes=false`
- Live trading/risk logic changed: **No**.
- Next: `meta_066_cron_consumer_missing_non_boolean_live_trading_flag_fixture` — add a read-only fixture for missing/non-boolean live-trading flag payloads in cron/reporting summaries.

## 2026-06-08 11:02:47 CST — meta_064 cron compact safety summary consumer

- Added `self_learn/scripts/meta_064_cron_safety_summary_smoke.py`, a read-only cron/operator smoke wrapper that runs `meta_058_daily_health_report.py --safety-summary json`, parses the compact safety payload, and emits one grep/chat-friendly `META_LABEL_CRON_CONSUMER ... live_trading_changes=false` line.
- Default exit remains `0` even for expected shadow-mode blockers so scheduled reporting does not fail noisily; `--strict-alert-exit` is available only for explicit operator smoke checks.
- Added `tests/test_meta_064_cron_safety_summary_smoke.py` covering expected shadow-blocked formatting, opt-in strict non-zero behavior, and the safe-consideration info classification.
- Updated `dev/meta_labeling/PLAN.md` with `meta_065` as the next read-only guard fixture step.
- Observed live read-only cron consumer result: `alert=expected_shadow_blocked`, `enforcement_safe=false`, `real_source_verified=false`, eligible real outcomes `0/100`; blockers were `insufficient_eligible_real_outcomes:0/100` and `meta_label_source_not_ok_events:2753`.
- Verification:
  - `python3 -m py_compile self_learn/scripts/meta_064_cron_safety_summary_smoke.py tests/test_meta_064_cron_safety_summary_smoke.py`
  - `python3 -m pytest -q tests/test_meta_064_cron_safety_summary_smoke.py` → `3 passed in 0.06s`
  - `python3 -m pytest -q tests/test_meta_058_daily_health_report.py tests/test_meta_064_cron_safety_summary_smoke.py` → `6 passed in 0.14s`
  - `python3 self_learn/scripts/meta_064_cron_safety_summary_smoke.py --days 1 --min-eligible-outcomes 100` → one-line `META_LABEL_CRON_CONSUMER ... live_trading_changes=false`
- Live trading/risk logic changed: **No**.
- Next: `meta_065_cron_consumer_live_trading_flag_guard_fixture` — add one more read-only regression guard for unexpected `live_trading_changes` classification.

## 2026-06-08 10:02:58 CST — meta_063 compact safety summary CLI mode

- Updated `self_learn/scripts/meta_058_daily_health_report.py` with `--safety-summary json|text`, printing only the compact read-only meta-label safety verdict instead of the full daily report when requested.
- Added helper functions for a compact JSON payload and grep-friendly one-line text output; both explicitly report `live_trading_changes=false` and do not touch DB/config/model/runtime logic.
- Extended `tests/test_meta_058_daily_health_report.py` with regression coverage for the compact payload and text formatting.
- Observed live read-only compact result: `enforcement_safe=false`, recommendation `keep_meta_label_enforcement_disabled`; blockers were `insufficient_eligible_real_outcomes:0/100` and `meta_label_source_not_ok_events:2720`; 1-day telemetry had `meta_label_gate=2720`, `trade_quality_gate=3889`, and eligible real outcomes remained `0` with `source_counts={"synthetic_seed": 100}`.
- Verification:
  - `python3 -m py_compile self_learn/scripts/meta_058_daily_health_report.py tests/test_meta_058_daily_health_report.py`
  - `python3 -m pytest -q tests/test_meta_058_daily_health_report.py` → `3 passed in 0.12s`
  - `python3 self_learn/scripts/meta_058_daily_health_report.py --days 1 --min-eligible-outcomes 100 --safety-summary text` → one-line `META_LABEL_SAFETY ... live_trading_changes=false`
  - `python3 self_learn/scripts/meta_058_daily_health_report.py --days 1 --min-eligible-outcomes 100 --safety-summary json` → compact JSON report returned `ok=true`, `enforcement_safe=false`, `live_trading_changes=false`
- Live trading/risk logic changed: **No**.
- Next: `meta_064_compact_safety_summary_cron_consumption_example` — add a small operator-facing snippet or smoke-test wrapper for consuming the compact summary while keeping it read-only.

## 2026-06-08 09:00:24 CST — meta_062 daily health safety-check integration

- Updated `self_learn/scripts/meta_058_daily_health_report.py` so the existing compact daily health report now embeds `meta_label_safety_summary` from the read-only `meta_061` safety check.
- Added `tests/test_meta_058_daily_health_report.py` covering both current blocked conditions (`source_ok=false`, insufficient eligible real outcomes) and the only safe-consideration path (shadow meta gate + verified paper/live provenance).
- Observed live read-only result: `enforcement_safe=false`, recommendation `keep_meta_label_enforcement_disabled`; blockers were `insufficient_eligible_real_outcomes:0/100` and `meta_label_source_not_ok_events:2697`; 1-day telemetry had `meta_label_gate=2697`, `trade_quality_gate=3707`, and eligible real outcomes remained `0` with `source_counts={"synthetic_seed": 100}`.
- Verification:
  - `python3 -m py_compile self_learn/scripts/meta_058_daily_health_report.py self_learn/scripts/meta_061_shadow_provenance_safety_check.py tests/test_meta_058_daily_health_report.py`
  - `python3 -m pytest -q tests/test_meta_058_daily_health_report.py tests/test_meta_061_shadow_provenance_safety_check.py` → `4 passed in 0.12s`
  - `python3 self_learn/scripts/meta_058_daily_health_report.py --days 1 --min-eligible-outcomes 100` → JSON report returned `ok=true`, `live_trading_changes=false`, `enforcement_safe=false`
- Live trading/risk logic changed: **No**.
- Next: `meta_063_daily_health_cli_compact_safety_summary` — add a small CLI-friendly compact output mode for this safety verdict while keeping all checks read-only.

## 2026-06-05 12:01:57 CST — meta_061 read-only shadow/provenance safety check

- Added `self_learn/scripts/meta_061_shadow_provenance_safety_check.py` as a read-only guardrail report combining `meta_056` gate telemetry with `meta_059` immutable provenance review.
- Added `tests/test_meta_061_shadow_provenance_safety_check.py` covering both blocked current-state conditions (`source_ok=false`, insufficient eligible real outcomes) and the only safe-consideration path (shadow meta gate + verified paper/live provenance).
- Observed live read-only result: `enforcement_safe=false`, recommendation `keep_meta_label_enforcement_disabled`; blockers were `insufficient_eligible_real_outcomes:0/100` and `meta_label_source_not_ok_events:1037`; 1-day telemetry had `meta_label_gate=1037` all `NO_DATA` and `trade_quality_gate=2418`.
- Verification:
  - `python3 -m py_compile self_learn/scripts/meta_061_shadow_provenance_safety_check.py tests/test_meta_061_shadow_provenance_safety_check.py self_learn/scripts/meta_056_gate_shadow_audit.py self_learn/scripts/meta_059_provenance_rows_review.py`
  - `python3 -m pytest -q tests/test_meta_061_shadow_provenance_safety_check.py` → `2 passed in 0.07s`
  - `python3 self_learn/scripts/meta_061_shadow_provenance_safety_check.py --days 1 --min-eligible-outcomes 100` → JSON report returned `ok=true`, `live_trading_changes=false`
- Live trading/risk logic changed: **No**.
- Next: `meta_062_daily_health_safety_check_integration` — integrate this safety verdict into the compact daily health wrapper while keeping all checks read-only.

## 2026-06-05 11:00:57 CST — meta_060 provenance review fixture test

- Added `tests/test_meta_059_provenance_rows_review.py` as a regression fixture for provenance eligibility classification.
- Covered the key safety cases: `synthetic_seed` rows with migration `provenance_meta` stay `non_real_or_synthetic`; `paper_broker` with `broker_order_id` is eligible; `live_broker` with `provenance_meta` is eligible; `paper_broker` without evidence is `real_source_missing_evidence`; missing source stays non-eligible even with a broker id; legacy schema without provenance columns remains blocked.
- Updated `dev/meta_labeling/PLAN.md` with the next placeholder read-only safety step (`meta_061`) so the hourly loop has a safe follow-up after meta_060.
- Verification:
  - `python3 -m py_compile tests/test_meta_059_provenance_rows_review.py self_learn/scripts/meta_059_provenance_rows_review.py`
  - `python3 -m pytest -q tests/test_meta_059_provenance_rows_review.py` → `2 passed in 0.08s`
  - `python3 -m json.tool dev/meta_labeling/STATUS.json >/tmp/meta_status_check.json`
- Live trading/risk logic changed: **No**.
- Next: `meta_061_read_only_shadow_provenance_safety_check` — add/refine another small read-only shadow/provenance safety check based on latest telemetry.

## 2026-06-05 10:03:28 CST — meta_059 provenance rows review

- Added `self_learn/scripts/meta_059_provenance_rows_review.py` as a read-only SQLite provenance audit for `outcomes` rows.
- The script uses immutable `mode=ro&immutable=1`, does not import live trading modules, and does not write DB/config/model artifacts.
- Observed result: `ok=true`; `schema_ready=true`; `total_outcomes=100`; `source_counts={"synthetic_seed": 100}`; `recorded_by_counts={"seed_synthetic_outcomes_legacy": 100}`; eligible paper/live rows with broker evidence = `0`; `real_source_verified=false`.
- Important nuance: all 100 rows have `provenance_meta` markers from the legacy synthetic migration, but none are `paper_broker` / `live_broker`, so they remain non-eligible for promotion/enforcement.
- Verification:
  - `python3 -m py_compile self_learn/scripts/meta_059_provenance_rows_review.py`
  - `python3 self_learn/scripts/meta_059_provenance_rows_review.py --sample-limit 3` → JSON report returned `ok=true`
  - `python3 -m json.tool dev/meta_labeling/STATUS.json >/tmp/meta_status_check.json`
- Live trading/risk logic changed: **No**.
- Next: `meta_060_provenance_review_fixture_test` — add a small regression fixture/test for provenance eligibility classification without touching runtime.

## 2026-06-05 09:01:08 CST — meta_058 daily health report wrapper

- Added `self_learn/scripts/meta_058_daily_health_report.py` as a compact read-only wrapper for hourly/daily meta-labeling telemetry.
- Combines: prediction/gate health from `logs/decisions.jsonl`, `meta_056_gate_shadow_audit.py` shadow gate audit, immutable read-only `trading_bot.db` provenance eligibility, and latest `training_log.jsonl` metrics.
- Observed `--days 1` result: report `ok=true`; `trade_quality_gate=1764`; `meta_label_gate=984`; meta-label remains `NO_DATA`; provenance `schema_ready=true` but `eligible_real_source_count=0` / `real_source_verified=false`; latest holdout accuracy `0.6`.
- Verification:
  - `python3 -m py_compile self_learn/scripts/meta_058_daily_health_report.py self_learn/scripts/meta_056_gate_shadow_audit.py`
  - `python3 self_learn/scripts/meta_058_daily_health_report.py --days 1` → JSON report returned `ok=true`
- Live trading/risk logic changed: **No**.
- Next: `meta_059_provenance_rows_review` — review whether any true broker/paper provenance rows exist; keep meta gate in shadow/no-data until evidence is durable.

## 2026-06-04 11:02:17 CST — meta_057 gate shadow audit fixture test

- Added `tests/test_meta_056_gate_shadow_audit.py` as a parser regression fixture for the read-only shadow gate audit script.
- Covered: malformed JSON lines ignored, malformed trade-quality score counted, HK/US market inference, shadow counts, decision/reason/symbol counters, `source_ok` counters, and missing-log response.
- Verification:
  - `python3 -m py_compile tests/test_meta_056_gate_shadow_audit.py self_learn/scripts/meta_056_gate_shadow_audit.py`
  - `python3 -m pytest -q tests/test_meta_056_gate_shadow_audit.py` → `2 passed in 0.03s`
- Live trading/risk logic changed: **No**.
- Next: `meta_058_daily_health_report_wrapper` — add a compact daily health report wrapper combining prediction health + gate shadow audit + provenance eligibility.

## 2026-06-04 10:03:58 CST — meta_056 gate shadow audit

- Added `self_learn/scripts/meta_056_gate_shadow_audit.py` (read-only; no imports from live trading modules, no DB/config/model writes).
- Purpose: summarize `trade_quality_gate` and `meta_label_gate` structured events from `logs/decisions.jsonl` so shadow gate telemetry can be tracked before any enforcement decision.
- Verification:
  - `python3 -m py_compile self_learn/scripts/meta_056_gate_shadow_audit.py`
  - `python3 self_learn/scripts/meta_056_gate_shadow_audit.py --days 1`
- Observed result: `trade_quality_gate=65`, `meta_label_gate=65`, all `shadow=true`; meta-label remains `NO_DATA` because `source_ok=false` / `insufficient_real_outcomes`.
- Live trading/risk logic changed: **No**.
- Next: `meta_057_gate_shadow_audit_fixture_test` — add a small parser regression fixture/test.
