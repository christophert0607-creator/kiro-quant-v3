---
name: GPU Training Expansion (Issue 25)
description: Workflow and setup guide for the RTX 3060 long-term training pipeline.
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
