---
name: Kiro Quant V3 — Training & Monitoring
description: GPU training pipeline and production monitoring guide for KiroQuant V3.
---

# GPU Training Expansion

## 1. Description
This skill outlines how to expand data, utilize GPU optimizations effectively, and manage a 24/7 training lifecycle for quantitative models.

## 2. Optimizations Used
- **Autocast / Mixed Precision**: Dramatically lowers VRAM cost. We utilize `bfloat16` if hardware supports it, otherwise fallback to `float16`.
- **Gradient Accumulation**: Mini-batch gradients are summed across multiple steps before performing the backward step, simulating an exponentially larger batch size without requiring simultaneous memory holding (VRAM bypass hack).
- **TensorFloat-32 (TF32)**: Specifically enabled for NVIDIA RTX 30/40 series GPUs to speed up cuDNN and matmul functions.

## 3. Workflow

- **Step 1: Data Initialization**
  Run `fetch_multi_stock_data.py` to ingest 20+ years of HK/US ticker data natively into feature engineering nodes (saves `.parquet`).

- **Step 2: Start Resilient Training**
  `bash start_training.sh` initializes a resilient workspace using tmux. The engine loop continues processing even if SSH/Terminal drops.

- **Step 3: Monitoring & Resuming**
  The script auto-logs to `v3_pipeline/logs/gpu_monitor.log`. Should the hardware crash, `trainer_v4_2_gpu.py` will auto-save periodic checkpoints to `v3_pipeline/models/` and supports a `--resume` path argument to load the previous state rather than restarting epochs from 0.

## 4. Commands Reference
- Run Data Sync: `python fetch_multi_stock_data.py`
- Force Single Trainer Run: `python trainer_v4_2_gpu.py --epochs 20`
- See tmux sessions: `tmux ls`
- Kill session (Hard stop): `tmux kill-session -t quant_pretrain`

---

# Production Monitoring & Self-Heal

## 5. Decision Trace Collection
- **Status**: ✅ Active (US SIM mode)
- **Location**: `learning/us_sim/decision_trace_us_sim.jsonl`
- **Frequency**: Every trading cycle
- **Also**: `learning/us_sim/account_snap_us_sim.jsonl` — 5-minute account snapshots

## 6. Cron Health Monitoring

| Job | Schedule | Checks | Model |
|-----|----------|--------|-------|
| `selfheal-detect` | every 4h | Cron failures → issue insertion + `oa collect` | antigravity/gemini-3-flash |
| `selfheal-fix` | 02/08/14/20h | Auto-fix detected issues | antigravity/gemini-3-flash |
| `health-check (quant)` | every 15m | V3 engine alive + restart if dead | antigravity/gemini-3-flash |
| `system-daily-health-check` | daily 09:00 HKT | OpenClaw status + daily check | antigravity/gemini-3-flash |
| `kiro-quant graphify rebuild` | daily 05:00 HKT | Codebase graph update | antigravity/gemini-3-flash |
| `Kiro Quant Daily Research` | daily 23:00 HKT | Research + Wiki ingest | antigravity/gemini-3-flash |

## 7. Alert Destination
All cron reports → Telegram `625655860`

## 8. V3 Auto-Restart
`V3 Heartbeat - Monitor & Restart if Dead` cron checks:
```bash
ps aux | grep 'v3_launcher\|python.*quant_v2' | grep -v grep
```
If process dead → restart via `nohup python3 v3_launcher.py`
If restart fails → Telegram alert "🔴 V3 重啟失敗，需手動介入"
