# Kiro Quant V3 - Long-term GPU Training Setup (Issue #25)

This repository extension introduces 24/7 long-term training capabilities utilizing RTX 3060 (12GB) hardware optimizations.

## 🚀 Key Improvements

1. **Massive Data Expansion**: Support for 50-100+ stocks (US + HK) generating 50GB-100GB of historical parity parquet files via `yfinance`.
2. **RTX 3060 Optimizations**:
   - Mixed Precision (bfloat16) to save VRAM and accelerate throughput.
   - Gradient Accumulation (effective batch size 256) without OOM.
   - Pytorch 2.0+ `torch.compile` standard.
   - TF32 utilization for matrix multiplications natively enabled.
3. **24/7 Training Reliability**: tmux scripts and dedicated resource monitors to prevent session deaths.

## 🔧 How to Use

1. **Fetch Latest Data**
   ```bash
   # Download 20y history for 60 symbol array (US & HK)
   python fetch_multi_stock_data.py --output-dir v3_pipeline/data/storage/base_20y
   ```

2. **Launch 24/7 Training Environment**
   ```bash
   bash start_training.sh
   ```
   This script creates a detached `tmux` session containing:
   - Window 0: Model Trainer runtime loop (auto restarts if crash)
   - Window 1: Persistent GPU Monitor evaluating health every 10s
   - Window 2: Blank shell for ad-hoc commands

3. **Check Status**
   - Reattach to view logs: `tmux attach -t quant_pretrain`
   - Detach again: `Ctrl+B, D`
   - Read GPU health manually: `tail -f v3_pipeline/logs/gpu_monitor.log`
