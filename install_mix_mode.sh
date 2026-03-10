#!/bin/bash
# Kiro Mix Mode Cron Installer
# 安裝美股休市時的自動化訓練任務

echo "🚀 正在部署 Kiro Mix Mode Cron Jobs..."

# 確保日誌目錄存在
mkdir -p ~/projects/kiro-quant-v3/logs
mkdir -p ~/projects/kiro-quant-v3/v3_pipeline/logs

# 寫入 crontab
crontab << 'EOF'
# Kiro Mix Mode - 美股休市自動化 (00:00-08:00 HK)
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
PYTHONPATH=/home/tsukii0607/projects/kiro-quant-v3

# 00:30 - 啟動全股票預訓練 (任務 A)
30 0 * * * cd ~/projects/kiro-quant-v3 && nohup /usr/bin/python3 v3_pipeline/models/trainer_all_symbols.py >> logs/train_all_$(date +\%Y\%m\%d).log 2>&1 &

# 02:00 - 啟動超參數搜索 (任務 B)
0 2 * * * cd ~/projects/kiro-quant-v3 && nohup /usr/bin/python3 v3_pipeline/models/hyperparam_search.py --trials 12 >> logs/hyper_$(date +\%Y\%m\%d).log 2>&1 &

# 04:00 - 啟動 RL 訓練 (任務 C)
0 4 * * * cd ~/projects/kiro-quant-v3 && nohup /usr/bin/python3 v3_pipeline/rl/rl_trainer.py --episodes 500 >> logs/rl_$(date +\%Y\%m\%d).log 2>&1 &

# 07:30 - 自動關閉所有訓練，準備開盤
30 7 * * * pkill -f trainer_all_symbols.py; pkill -f hyperparam_search.py; pkill -f rl_trainer.py; echo "$(date): Training stopped for market open" >> ~/projects/kiro-quant-v3/logs/cron.log 2>&1

# 08:00 - 執行 Git 提交（保存昨晚成果）
0 8 * * * cd ~/projects/kiro-quant-v3 && git add models/ reports/ logs/ && git commit -m "[Auto] Nightly training $(date +\%Y-\%m-\%d)" && git push origin main >> logs/git_$(date +\%Y\%m\%d).log 2>&1

# 每小時記錄系統狀態
0 * * * * cd ~/projects/kiro-quant-v3 && /usr/bin/python3 v3_pipeline/utils/gpu_monitor.py --once >> logs/gpu_$(date +\%Y\%m\%d).log 2>&1
EOF

echo "✅ Cron Jobs 已安裝！"
echo ""
echo "📋 當前排程："
crontab -l | grep -v "^#" | grep -v "^$"
echo ""
echo "🕐 時間段說明："
echo "  00:30 - 啟動全股票訓練 (A)"
echo "  02:00 - 啟動超參數搜索 (B)"
echo "  04:00 - 啟動 RL 訓練 (C)"
echo "  07:30 - 自動停止所有訓練"
echo "  08:00 - Git 提交昨晚成果"
echo ""
echo "💡 美股開盤時 (16:00+ HK) 切換為 Session 實時模式"
