# Weekend 24h Training Progress Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 星期六、星期日將 V3 由交易優先切換成「24 小時訓練 / 回填 / 評估優先」，並把 `self_learn/PROGRESS.md` 改成顯示訓練進度、質量指標與阻塞原因。

**Architecture:** 新增一個 weekend training runner，由 cron / Hermes cron 在週六日啟動；runner 只做 data backfill、indicator warmup、self-learn retrain dry-run / guarded retrain、prediction health report、progress append。Trading runtime 不做 live order 變更；所有模型 promotion 必須經現有 provenance guard，synthetic-only 不可升級 live model。

**Tech Stack:** Python 3, Bash cron wrapper, SQLite (`self_learn/trading_bot.db`, `kiro_quant.db`), XGBoost self_learn retrain, `scripts/report_prediction_health.py`, `self_learn/PROGRESS.md`, Hermes cronjob.

---

## 核心策略：週末 24h Training Mode

### 時段定義
- **啟動：** 週六 00:00 HKT
- **停止：** 週一 07:30 HKT 前
- **週六/日全天：** 交易市場多數關閉，主力跑訓練、回填、報告
- **週一開市前：** 停止 long training loop，只保留健康檢查，避免干擾交易 session

### 安全原則
1. **不自動開真倉 / 不改交易方向。**
2. **不因 synthetic-only metric 升級模型。**
3. **每輪訓練先 snapshot，再 dry-run，再 guarded retrain。**
4. **訓練失敗要寫入 `PROGRESS.md`，不可靜默。**
5. **每 2 小時一次 progress heartbeat；每 6 小時一次深度訓練循環。**
6. **如果 FutuOpenD / V3 正在 live session，不跑重訓，只寫 skip reason。**

---

## 24 小時訓練節奏

### 每 30 分鐘：輕量資料收集
- 檢查 DB row counts
- 檢查 `self_learn/trading_bot.db` schema / provenance columns
- 檢查 `kiro_quant.db` market_data row count
- append progress line：`collector_ok / collector_blocked`

### 每 2 小時：Training Progress Update
- 跑 `scripts/report_prediction_health.py --days 1`
- 跑 read-only stats：predictions / signals / outcomes / eligible real outcomes
- 寫入 `self_learn/PROGRESS.md`
- Telegram 簡短摘要：只報有變化 / blocker / completed stage

### 每 6 小時：Deep Training Cycle
順序：
1. `python3 self_learn/backfill_indicators.py --dry-run`
2. `python3 self_learn/scripts/train_trade_outcome_head.py --dry-run`
3. `python3 -c "from self_learn.retrain import retrain_pipeline; ..."` guarded retrain
4. 讀 `self_learn/models/training_log.jsonl` 最新 metrics
5. 寫 `PROGRESS.md`：samples、accuracy、win_rate、eligible_real_source_count、guard status

### 每日 23:30 HKT：Weekend Summary
- 匯總當日訓練輪次
- 成功 / 失敗 / skip count
- 最新 holdout metrics
- 明確標記：`synthetic_only` vs `paper_broker/live_broker`

---

## PROGRESS.md 新格式

週末開始後，`self_learn/PROGRESS.md` 每段改為：

```markdown
## [2026-06-06 14:00 HKT] Weekend Training Cycle
**Mode:** weekend_training_24h
**Cycle:** deep_training / progress_update / collector
**DB:** predictions=N | signals=N | closed=N | outcomes=N
**Real Eligible Outcomes:** N / required=100 | source_verified=true/false
**Retrain Guard:** pass/blocked | reason=...
**Metrics:** accuracy=... | win_rate=... | samples=... | iterations=... | early_stopped=...
**Artifacts:** written=false/true | model_path=...
**Risk:** synthetic_only / schema_blocked / data_gap / runtime_busy
**Next:** next scheduled step
```

---

## Task 1: Create read-only weekend training status script

**Objective:** 產生單一 JSON 狀態，供 runner / PROGRESS / Telegram 使用。

**Files:**
- Create: `self_learn/scripts/weekend_training_status.py`
- Test: `tests/test_weekend_training_status.py`

**Step 1: Write failing test**

```python
def test_weekend_training_status_reports_required_keys(tmp_path):
    from self_learn.scripts.weekend_training_status import build_status
    status = build_status(workspace='.')
    for key in ['mode', 'stats', 'eligible_real_source_count', 'guard', 'latest_metrics']:
        assert key in status
```

**Step 2: Run fail**

```bash
PYTHONPATH=. pytest tests/test_weekend_training_status.py::test_weekend_training_status_reports_required_keys -q
```

Expected: FAIL because module does not exist.

**Step 3: Implement minimal script**

Script responsibilities:
- read `self_learn/trading_bot.db` read-only immutable when possible
- count predictions/signals/outcomes
- count eligible real outcomes where `source in ('paper_broker','live_broker')` and broker evidence exists
- read latest `self_learn/models/training_log.jsonl`
- print JSON

**Step 4: Verify**

```bash
python3 self_learn/scripts/weekend_training_status.py | python3 -m json.tool
PYTHONPATH=. pytest tests/test_weekend_training_status.py -q
```

---

## Task 2: Create weekend training runner

**Objective:** 跑一輪 collector/progress/deep_training，append `PROGRESS.md`。

**Files:**
- Create: `self_learn/scripts/weekend_training_runner.py`
- Test: `tests/test_weekend_training_runner.py`

**CLI:**

```bash
python3 self_learn/scripts/weekend_training_runner.py --mode collector
python3 self_learn/scripts/weekend_training_runner.py --mode progress
python3 self_learn/scripts/weekend_training_runner.py --mode deep --max-seconds 1800
```

**Runner behavior:**
- `collector`: only status JSON + append progress
- `progress`: status + prediction health + append progress
- `deep`: backfill dry-run + trade outcome head dry-run + guarded retrain + append progress

**Safety checks before deep:**
- if weekday not Sat/Sun and not `--force`: skip
- if current time is Monday before market prep: skip
- if `ps aux` has live trading session and market mode HK/US: skip
- if DB lock / WAL busy: skip and report

---

## Task 3: Update PROGRESS writer

**Objective:** 將週末 progress 改成訓練進度，而不是普通 cron stats。

**Files:**
- Modify: `self_learn/scripts/weekend_training_runner.py`
- Optional Modify: `self_learn/cron_self_learn.sh`

**Append format:** use the template above.

**Verification:**

```bash
python3 self_learn/scripts/weekend_training_runner.py --mode collector --dry-run-progress
```

Expected:
- prints markdown block
- does not modify file in dry-run

Then:

```bash
python3 self_learn/scripts/weekend_training_runner.py --mode collector
 tail -30 self_learn/PROGRESS.md
```

Expected: latest block has `Mode: weekend_training_24h`.

---

## Task 4: Add weekend cron / Hermes cron schedule

**Objective:** 週六日自動跑 24h training loop。

**Preferred Hermes cron jobs:**

1. Collector every 30 minutes, Sat/Sun:
```text
*/30 * * * 6,0
```
Prompt/script:
```bash
cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3 && python3 self_learn/scripts/weekend_training_runner.py --mode collector
```

2. Progress every 2 hours, Sat/Sun:
```text
0 */2 * * 6,0
```

3. Deep training every 6 hours, Sat/Sun:
```text
0 */6 * * 6,0
```

4. Daily weekend summary 23:30 Sat/Sun:
```text
30 23 * * 6,0
```

**Important:** Cron output 必須繁中摘要，唔可以 silent。

---

## Task 5: Guarded retrain pipeline integration

**Objective:** Deep cycle 可以訓練，但不可亂寫 live model。

**Rules:**
- Always run `train_trade_outcome_head.py --dry-run` first.
- If guard blocked because `eligible_real_source_count < 100`, mark progress as `blocked.synthetic_or_insufficient_real_outcomes`.
- If guard pass, then allow `retrain_pipeline()` write artifacts.
- Record before/after counts:
  - `self_learn/models/model_*.pkl`
  - `self_learn/models/meta_*.json`
  - `self_learn/models/training_log.jsonl` size

**Verification:**

```bash
python3 self_learn/scripts/weekend_training_runner.py --mode deep --dry-run
```

Expected: no model artifact count change.

---

## Task 6: Weekend stop / weekday handoff

**Objective:** 週一開市前停止 24h training，避免同交易 runtime 爭資源。

**Files:**
- Add runner guard only; no need to kill V3.

**Rule:**
- Monday 07:30 HKT onwards: `deep` mode returns skip.
- Weekdays: only `collector` can run, `deep` blocked unless `--force`.

**Verification:** unit test with monkeypatched datetime.

---

## Task 7: Runtime verification checklist

Run after implementation:

```bash
cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3
python3 -m py_compile self_learn/scripts/weekend_training_status.py self_learn/scripts/weekend_training_runner.py
PYTHONPATH=. pytest tests/test_weekend_training_status.py tests/test_weekend_training_runner.py -q
python3 self_learn/scripts/weekend_training_runner.py --mode collector
python3 self_learn/scripts/weekend_training_runner.py --mode progress
python3 self_learn/scripts/weekend_training_runner.py --mode deep --dry-run
```

Expected:
- py_compile OK
- pytest pass
- PROGRESS.md append OK
- dry-run deep does not write artifacts
- blocked reason explicit if provenance insufficient

---

## Final Acceptance Criteria

1. 週六日 24h 內每 30 分鐘有 collector status。
2. 每 2 小時 `PROGRESS.md` 有 training progress block。
3. 每 6 小時 deep training cycle 有 dry-run/guarded retrain result。
4. 所有 synthetic-only / insufficient real outcomes 都會 blocked，不會 promotion。
5. Telegram / Hermes final output 每次用繁中摘要。
6. 週一 07:30 HKT 後 deep training 自動 skip。
7. 不影響 V3 live trading / FutuOpenD / exit alert logic。

---

## Suggested Weekend Operating Mode

**今晚可直接做：**
- 先實作 Task 1–3。
- 建 Hermes cron：collector/progress/deep/summary。
- 今晚先行一輪 `--mode deep --dry-run`，確認 blocked reason。

**如果你想進取啲：**
- 週末可以加 backfill / feature repair / model calibration experiments，但全部要寫入 `reports/weekend_training/YYYY-MM-DD/`，唔直接影響 live model。

**銳評：** 週末 24h training 最有價值唔係盲目跑 XGBoost，而係「補足真實 broker outcome provenance + 修 feature quality + 固定產出 progress」。如果仍然 synthetic-only，就算跑 24 小時都唔應該自動升級模型，只可以當 research / calibration。
