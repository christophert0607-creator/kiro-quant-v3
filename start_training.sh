#!/bin/bash
# 啟動 24/7 訓練環境 (GPU Long-Term Training)

SESSION_NAME="quant_pretrain"

# Check if session exists
tmux has-session -t $SESSION_NAME 2>/dev/null

if [ $? != 0 ]; then
  echo "🚀 Creating new tmux session: $SESSION_NAME"
  tmux new-session -d -s $SESSION_NAME
  
  # Window 0: Training process (Auto-restart loop)
  tmux rename-window -t $SESSION_NAME:0 'Training'
  tmux send-keys -t $SESSION_NAME:0 "
while true; do
    echo 'Starting GPU Trainer...';
    python trainer_v4_2_gpu.py --data-path v3_pipeline/data/storage/base_20y --epochs 100 --batch 64;
    echo 'Trainer exited or crashed. Restarting in 10s...';
    sleep 10;
done
" C-m
  
  # Window 1: Monitor
  echo "📊 Setting up Monitor window"
  tmux new-window -t $SESSION_NAME:1 -n 'Monitor'
  tmux send-keys -t $SESSION_NAME:1 "python gpu_monitor.py" C-m
  
  # Window 2: Dashboard/Shell
  tmux new-window -t $SESSION_NAME:2 -n 'Shell'
  tmux send-keys -t $SESSION_NAME:2 "cd v3_pipeline && ls -la" C-m
  
  echo "✅ Session created. Attaching..."
else
  echo "⚡ Session $SESSION_NAME already exists. Attaching..."
fi

tmux attach-session -t $SESSION_NAME
