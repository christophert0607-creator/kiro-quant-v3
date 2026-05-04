#!/bin/bash
# Self-Learning System Cron Job
# Runs every 4 hours to check stats, run retrain if needed, write progress

WORKSPACE="/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3"
LOG_FILE="$WORKSPACE/self_learn/cron_log.txt"
PROGRESS_FILE="$WORKSPACE/self_learn/PROGRESS.md"
RESULT_FILE="$WORKSPACE/self_learn/.cron_result.json"

echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] Self-Learn Cron ===" >> "$LOG_FILE"

cd "$WORKSPACE" || exit 1

# 1. Check stats
STATS=$(python3 -c "
import json
from self_learn.models import get_stats
print(json.dumps(get_stats(), default=str))
" 2>&1)
echo "STATS: $STATS" >> "$LOG_FILE"

# 2. Check and retrain if needed — write to temp file to avoid shell substitution issues
python3 -c "
import json
import sys
from self_learn.models import get_stats
from self_learn.feedback import should_retrain
from self_learn.retrain import retrain_pipeline

try:
    stats = get_stats()
    should_r = should_retrain()
    result = {
        'stats': stats,
        'should_retrain': should_r,
        'retrain_done': False,
    }

    if should_r:
        retrain_result = retrain_pipeline()
        result['retrain_done'] = retrain_result.get('status') == 'ok'
        result['retrain_result'] = retrain_result

    print(json.dumps(result, default=str))
except Exception as exc:
    print(json.dumps({'error': str(exc)}))
" > "$RESULT_FILE" 2>&1
RESULT=$(cat "$RESULT_FILE")
echo "RETRAIN_CHECK: $RESULT" >> "$LOG_FILE"

# 3. Parse for progress file — robust: handle empty/corrupt RESULT_FILE
PY_ENTRY=$(python3 -c "
import json
import sys
from datetime import datetime, timezone

try:
    raw = open('$RESULT_FILE').read()
    if not raw.strip():
        raise ValueError('Empty result file')
    result = json.loads(raw)
except Exception as exc:
    print(f'CRON_JSON_PARSE_FAIL: {exc}')
    sys.exit(0)

stats = result.get('stats', {})
should_r = result.get('should_retrain', False)
retrain_done = result.get('retrain_done', False)
retrain_res = result.get('retrain_result', {})

if retrain_res and isinstance(retrain_res, dict):
    metrics = retrain_res.get('metrics', {})
    retrain_line = f'**Retrain:** accuracy={metrics.get(\"accuracy\",0):.4f} | win_rate={metrics.get(\"win_rate\",0):.4f} | samples={metrics.get(\"total_samples\",0)}'
else:
    retrain_line = ''

entry = f'''
## [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}]
**Stats:** {stats.get('total_predictions',0)} predictions | {stats.get('total_signals',0)} signals | {stats.get('closed_signals',0)} closed | PnL={stats.get('total_pnl',0.0)}
**Should Retrain:** {should_r} | **Retrain Done:** {retrain_done}
'''
if retrain_line:
    entry += retrain_line + '\n'

print(entry)
" 2>&1)

echo "$PY_ENTRY" >> "$PROGRESS_FILE"
rm -f "$RESULT_FILE"
echo "---" >> "$LOG_FILE"
echo "Done at $(date)"
