# Kiro Quant System Review + 2-Week Blueprint

_Date: 2026-03-21_

## ClawTeam Review Context
- Team: `kiro-audit-20260321-001`
- Workers spawned:
  - `arch-auditor` — architecture / completeness audit
  - `health-auditor` — health checks / runtime risk audit
  - `planner` — 2-week blueprint draft
- Mode: read-only review (no code changes)

## Executive Summary
Kiro Quant is **already a serious paper-trading/research system**, not just a toy script. The repo has a real V3 pipeline, risk modules, backtest pieces, dry-run support, watchdog/logging artifacts, and a non-trivial test suite.

But it is **not yet fully production-complete as a live trading platform**. The biggest gaps are not in raw strategy ideas; they are in **operational completeness**:
- environment reproducibility
- test execution readiness
- config drift control
- persistence / audit trail coverage
- CI / release discipline
- secrets handling
- repo hygiene

### Practical score
- **Research / paper trading completeness:** **7.2 / 10**
- **Production / live trading completeness:** **4.8 / 10**

The system is closest to: **“strong prototype with live-system ambitions”**.

---

## What was verified
### Safe health checks run
- `python3 -m compileall v3_launcher.py v3_pipeline` ✅
- `python3 v3_launcher.py --dry-run` ✅
- `python3 v3_launcher.py --dry-run --profile standard` ✅
- import checks for key modules ✅
  - `v3_launcher`
  - `data_manager`
  - `state_store`
  - `v3_pipeline.core.main_loop`
  - `v3_pipeline.core.futu_connector`
  - `v3_pipeline.risk.manager`

### Notable runtime finding
- `python3 -m pytest --collect-only -q tests` ❌ failed because **pytest is not installed in the current runtime**.

### Persistence snapshot
- `kiro_quant.db` exists
- current SQLite tables observed: **`market_data` only**

---

## Strengths
### 1) Real pipeline shape exists
The repo is not flat or ad hoc. It has recognizable domains:
- `v3_pipeline/core`
- `v3_pipeline/data`
- `v3_pipeline/features`
- `v3_pipeline/models`
- `v3_pipeline/risk`
- `backtest_engine`
- `tests`

That matters: it means the system has room to harden, not just rewrite.

### 2) Dry-run path is healthy
Both lite and standard dry-run flows work, which is a strong sign that:
- config parsing is alive
- launcher wiring is not broken
- the system can be safely probed without firing trades

### 3) Risk-thinking already exists
The repo clearly contains non-trivial risk controls and related docs / modules:
- Kelly sizing
- transaction cost filter
- VaR / CVaR mentions
- ROR gating
- stop-loss / take-profit logic

### 4) Test intent exists
There are **15 test files** already present. That means the team is not starting from zero on quality discipline.

### 5) Operational logging exists
There are many runtime review logs and watchdog-style artifacts, which suggests the system has already been operated repeatedly, not just coded once.

---

## Key gaps / completeness blockers
### 1) Environment reproducibility is weak
Current packaging / environment story is incomplete:
- `requirements.txt` is extremely thin (`futu-api`, `pandas`)
- no `pyproject.toml`
- no `setup.py`
- no `Makefile`
- test runner not available in the runtime (`pytest` missing)

**Impact:** onboarding, CI, and reproducible deployment are fragile.

### 2) Config drift is already visible
Observed drift between `config.example.json`, docs, and `config.json`:
- `config.example.json` includes `futu_api_config` and `market_defaults`; current `config.json` does not
- docs / workflow mention `v3_live.runtime_profile`; current `config.json` is missing that key
- docs describe richer data fallback expectations; current config only shows `['yfinance']`

**Impact:** the documented operating model and the actual runtime model are diverging.

### 3) Persistence / audit trail is underbuilt
Database currently appears to contain only `market_data`.
Missing or unclear first-class persistence for:
- orders
- fills / executions
- positions snapshots
- PnL history
- decision logs
- risk events / blocked trades
- alerts / notifier events

**Impact:** if something goes wrong, post-mortem and compliance-style traceability are weak.

### 4) Repo hygiene is poor for a production-bound system
Top-level repo includes very heavy local environments / backups / generated artifacts:
- `v3_venv`
- `venv_py314_final_backup`
- DB files
- PNG charts
- runtime logs
- `_codex_tmp`
- `__pycache__`

Also `.gitignore` is too weak for the current repo shape.

**Impact:** noisy diffs, slow review, accidental commits, bloated backups, and harder automation.

### 5) Secrets handling is risky
Import checks emitted:
- `INFOWAY_API_KEY not set in environment — using hardcoded fallback key`

**Impact:** this is fine for local dev experiments, but not acceptable as a production habit.

### 6) CI / release discipline is missing
No `.github/workflows` detected.

**Impact:** there is no reliable automated gate for:
- import health
- compile check
- test suite
- lint
- config schema validation

### 7) Live trading readiness is still partial
The system talks like a production system, but operational evidence suggests it is still not fully there yet:
- broker readiness not fully codified into automated checks
- fallback chain not fully reflected in active config
- database model too thin for real execution traceability
- no clear release / rollback / promotion path

---

## Completeness matrix
| Area | Score | Notes |
|---|---:|---|
| Core architecture | 8/10 | Pipeline structure is real and reasonably modular |
| Runtime launcher health | 8/10 | Dry-run and compile checks pass |
| Strategy / risk surface | 7/10 | Good breadth, but needs verification discipline |
| Testability | 4/10 | Tests exist, but runner/env is not ready |
| Config discipline | 5/10 | Example/config/docs drift exists |
| Persistence / auditability | 4/10 | DB coverage too narrow |
| Observability | 6/10 | Logs exist, but structured health / event lineage is incomplete |
| Deployment reproducibility | 3/10 | Missing packaging, CI, and clean environment contract |
| Security / secrets hygiene | 4/10 | Hardcoded fallback key warning is a real concern |
| Live trading readiness | 5/10 | Serious groundwork, but not yet trustworthy enough |

---

## Top 5 missing pieces
1. **A real environment contract**
   - lock dependencies
   - install test/dev deps
   - define one canonical setup path

2. **Execution + risk audit tables**
   - orders, fills, positions, PnL, risk blocks, alerts

3. **CI health gate**
   - compile, import, tests, config validation, maybe smoke dry-run

4. **Config schema + drift prevention**
   - one source of truth and validation for runtime config

5. **Secrets / ops hardening**
   - remove fallback key habit, formalize `.env` requirements, preflight checks

---

## Recommended product framing right now
For the next two weeks, do **not** chase new alpha features first.

The correct move is:
> turn Kiro Quant from a promising trading codebase into a dependable trading system.

That means prioritizing **system integrity over strategy novelty**.

---

# Two-Week Development Blueprint

## Goal for the next 14 days
By the end of two weeks, Kiro Quant should be able to claim:
- reproducible setup from scratch
- smoke-tested dry-run path
- basic CI gate
- auditable paper-trading event trail
- one canonical config model
- a short production-readiness checklist

## North-star outcomes
1. New machine bootstrap succeeds in under 20 minutes
2. `dry-run` can be validated automatically
3. Paper-trading decisions are persisted and queryable
4. Tests run in one standard command
5. Config differences are explicit and validated
6. Repo becomes clean enough for disciplined iteration

---

## Workstreams
### WS1 — Environment & repo hardening
**Objective:** make the project reproducible and less chaotic.

**Deliverables**
- add `pyproject.toml` or fuller `requirements-dev.txt`
- define canonical setup docs
- expand `.gitignore`
- remove / isolate giant local env folders and generated artifacts from repo discipline
- standardize one Python version target

**Success metric**
- fresh environment can install and run dry-run without guesswork

### WS2 — Test & CI foundation
**Objective:** make quality checks executable, not aspirational.

**Deliverables**
- ensure `pytest` and core test deps are installable
- add smoke CI workflow
- standard commands for compile / import / dry-run / tests

**Success metric**
- one CI run passes on every branch push

### WS3 — Config governance
**Objective:** eliminate config drift.

**Deliverables**
- define config schema / validator
- align `config.example.json`, `config.json`, and docs
- make `runtime_profile` explicit
- formalize data source fallback configuration

**Success metric**
- invalid / drifting config fails fast before runtime

### WS4 — Persistence & audit trail
**Objective:** make trading behavior reconstructable.

**Deliverables**
- add tables for orders, fills, positions, PnL, risk events, alerts
- persist blocked trade reasons
- persist decision metadata per signal / order attempt

**Success metric**
- one paper-trading cycle can be replayed from DB + logs

### WS5 — Production readiness checklist
**Objective:** reduce operational ambiguity.

**Deliverables**
- preflight checklist for OpenD / broker / env / market state
- secrets policy cleanup
- runbook for failure / restart / rollback
- “paper-ready” vs “live-ready” criteria

**Success metric**
- anyone on the team can answer: “Is the system safe to start today?”

---

## Suggested 10-business-day plan

### Day 1 — Baseline freeze
**Tasks**
- snapshot current repo status
- document exact commands that currently pass/fail
- list all generated / local-only artifacts that should be ignored or moved
- decide canonical Python version

**Exit criteria**
- a baseline audit doc exists
- team agrees what “clean” means

### Day 2 — Environment contract
**Tasks**
- introduce `requirements-dev.txt` or `pyproject.toml`
- include pytest + tooling
- document setup in README / ops doc

**Exit criteria**
- a new machine can install dependencies consistently

### Day 3 — Repo hygiene sweep
**Tasks**
- strengthen `.gitignore`
- quarantine local venvs / backups / generated charts / temp files
- define storage locations for runtime artifacts

**Exit criteria**
- `git status` noise is significantly reduced

### Day 4 — CI smoke lane
**Tasks**
- add GitHub Actions workflow
- run compileall, import smoke, dry-run smoke, selected fast tests

**Exit criteria**
- PRs have a basic health gate

### Day 5 — Test runner normalization
**Tasks**
- ensure all current tests can be discovered and run
- categorize tests: unit / integration / broker-dependent
- mark slow or broker-bound tests clearly

**Exit criteria**
- `python3 -m pytest` works in the standard dev environment

### Day 6 — Config unification
**Tasks**
- align `config.example.json` and active config contract
- make `runtime_profile` explicit
- document source fallback policy and broker requirements

**Exit criteria**
- config schema + docs + runtime keys are aligned

### Day 7 — Persistence schema expansion
**Tasks**
- create DB schema for orders / fills / positions / PnL / risk events / alerts
- define minimal migration path

**Exit criteria**
- DB can record a complete paper-trading decision lifecycle

### Day 8 — Execution audit plumbing
**Tasks**
- write event logging hooks at decision, gate, order submit, fill, exit
- persist risk-block reasons and order outcomes

**Exit criteria**
- one trade attempt is fully reconstructable

### Day 9 — Ops hardening
**Tasks**
- remove hardcoded fallback key behavior or gate it behind explicit dev mode
- add startup preflight for env vars / broker connection / mode safety
- document restart and failure procedure

**Exit criteria**
- unsafe startup states fail loudly

### Day 10 — Readiness review
**Tasks**
- run end-to-end paper-trading rehearsal
- produce a short readiness scorecard
- decide whether next sprint should focus on execution quality, broker robustness, or strategy upgrades

**Exit criteria**
- paper-trading readiness can be defended with evidence

---

## Priority order if time is tight
If only half the blueprint gets done, do these first:
1. Environment contract + pytest installability
2. CI smoke lane
3. Config unification
4. Persistence for orders / fills / risk events
5. Secrets / preflight hardening

---

## What should wait until after this 2-week sprint
These are valuable, but not the right first move:
- new RL modules
- new LLM strategy generation layers
- new fancy dashboard work
- more models without stronger validation discipline
- multi-broker expansion before current auditability is fixed

---

## Final recommendation
Kiro Quant does **not** need another big burst of cleverness right now.
It needs **operational adulthood**.

If the next two weeks are spent on reproducibility, testability, auditability, and startup safety, then later strategy work will compound instead of destabilize the system.
