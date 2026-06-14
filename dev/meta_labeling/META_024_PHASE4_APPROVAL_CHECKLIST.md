# Meta-Labeling Phase 4 Approval Checklist

Generated: `2026-06-12T02:02:41.022212+00:00`

This is a read-only/report-only artifact. It does **not** approve live integration.

- Approval required before live integration: `True`
- Live trading/risk logic changed: `False`

## Approval gates
### gate_user_approval
- Category: `approval`
- Requirement: Named user approval is recorded before modifying LiveTradingLoop, order routing, sizing, stop-loss, or risk enforcement.
- Evidence: Approval note or ticket linked in DEVLOG/STATUS before any Phase 4 code change.

### gate_metric_bundle
- Category: `promotion_metric`
- Requirement: Promotion decision uses coverage, covered accuracy, abs-PnL weighted accuracy, and P&L delta together; raw accuracy alone is not sufficient.
- Evidence: Latest read-only metric study report attached with synthetic/live-data provenance clearly labelled.

### gate_shadow_first
- Category: `shadow_mode`
- Requirement: Initial integration runs in shadow mode only: log would-confirm/would-reject/would-reverse decisions without changing submitted orders.
- Evidence: Shadow report demonstrates no order/risk mutation and includes decision distribution by symbol/action.

### gate_replay_smoke
- Category: `verification`
- Requirement: Replay/smoke tests cover CONFIRM, REJECT, REVERSE, NO_DATA, missing payloads, malformed payloads, and rollback-off behavior.
- Evidence: Focused pytest output and py_compile commands recorded in DEVLOG.

### gate_operator_observability
- Category: `monitoring`
- Requirement: Operator-visible summary reports live_trading_changes=false/true explicitly, enforcement state, blockers, and safe fallback status.
- Evidence: Cron/chat-friendly one-line safety summary sample checked into dev/meta_labeling or self_learn/scripts.

## Shadow-mode guardrails
### guardrail_no_order_mutation
- Requirement: Shadow-mode decisions must not mutate order side, quantity, price, stop-loss, or broker submission path.
- Evidence: Tests assert order intent before/after shadow evaluator is unchanged.

### guardrail_fail_open_base_strategy
- Requirement: Missing/invalid meta-labeling data falls back to base strategy behavior and records a blocker instead of enforcing a meta decision.
- Evidence: Malformed and missing safety payload tests pass.

### guardrail_config_kill_switch
- Requirement: A config kill switch can disable all meta-labeling enforcement without redeploying live trading code.
- Evidence: Rollback-off smoke test and operator runbook entry exist.

## Rollback triggers
### rollback_any_live_order_delta_without_approval
- Requirement: Any live order delta caused by meta-labeling before explicit approval triggers immediate disable and investigation.
- Evidence: Alert/runbook names owner, command, and verification query.

### rollback_metric_regression
- Requirement: Shadow metrics regress below approved gate thresholds for coverage, weighted accuracy, or P&L delta over the agreed window.
- Evidence: Daily health report shows threshold breach and blocker state.

### rollback_payload_integrity
- Requirement: Malformed/missing safety payloads, non-boolean live_trading_changes flags, or stale telemetry trigger safe fallback.
- Evidence: Cron safety summary emits critical alert and enforcement_safe=false.

## Blocked without explicit user approval
- Do not edit v3_pipeline/core/main_loop.py for meta-labeling enforcement without user approval.
- Do not change live order routing, broker calls, position sizing, stop-loss, risk checks, or runtime enforcement logic.
- Do not promote synthetic-only results as live-ready without clear provenance and user approval.

Recommended next task: `meta_025_shadow_mode_summary_fixture`
