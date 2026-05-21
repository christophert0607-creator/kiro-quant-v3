#!/usr/bin/env python3
"""
meta_002 深度診斷 — 預測ID追蹤問題

目標：確認 LiveTradingLoop 為何 _pred_id_by_symbol 沒有正確傳遞 prediction_id 到 hook_on_signal

問題：
  - predictions 表有 10,871 條記錄
  - signals 表有 3 條記錄，但全部 prediction_id 為 NULL
  - 懷疑：_pred_id_by_symbol[symbol] 在 hook_on_signal 調用時是 None 或 key 不存在

檢查：
  1. predictions 表中是否有 predictions 帶有 id
  2. main_loop.py 中 _pred_id_by_symbol 的設置時機
  3. 信號寫入時 prediction_id 是否真的被傳遞

用法：
    cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3
    python3 dev/meta_labeling/scripts/diagnose_pred_id_tracking.py
"""

import sqlite3
import re
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/self_learn/trading_bot.db")
MAIN_LOOP_PATH = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/v3_pipeline/core/main_loop.py")

def check_predictions_have_ids():
    """確認 predictions 表中所有記錄都有有效 ID"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM predictions WHERE id IS NULL")
    null_ids = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM predictions")
    total = cursor.fetchone()[0]

    # 顯示最近幾條預測的 ID 格式
    cursor.execute("SELECT id, symbol, predicted_price FROM predictions ORDER BY created_at DESC LIMIT 3")
    recent = cursor.fetchall()

    conn.close()

    return {
        "total": total,
        "null_ids": null_ids,
        "sample_ids": [(str(r[0])[:20], r[1], r[2]) for r in recent]
    }

def check_pred_id_tracking_in_code():
    """分析 main_loop.py 中 _pred_id_by_symbol 的設置與使用"""
    if not MAIN_LOOP_PATH.exists():
        return {"error": "main_loop.py not found"}

    content = MAIN_LOOP_PATH.read_text()
    lines = content.split('\n')

    results = {
        "store_operations": [],  # _pred_id_by_symbol[symbol] = ...
        "retrieve_operations": [],  # self._pred_id_by_symbol.get(...)
        "check_operations": [],  # if symbol in self._pred_id_by_symbol
    }

    # 找出所有涉及 _pred_id_by_symbol 的行
    for i, line in enumerate(lines, 1):
        if '_pred_id_by_symbol' in line:
            context = line.strip()
            if 'self._pred_id_by_symbol[symbol]' in line and '=' in line:
                results["store_operations"].append((i, context))
            elif 'self._pred_id_by_symbol.get' in line:
                results["retrieve_operations"].append((i, context))
            elif 'in self._pred_id_by_symbol' in line:
                results["check_operations"].append((i, context))

    return results

def trace_pred_hook_call():
    """確認 hook_on_prediction 的返回值是否為 None"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # 檢查最近預測是否有 ID
    cursor.execute("""
        SELECT id, symbol, predicted_price, confidence, created_at
        FROM predictions
        ORDER BY created_at DESC
        LIMIT 5
    """)
    recent_preds = cursor.fetchall()

    # 檢查 predictions 和 signals 的時間差
    cursor.execute("""
        SELECT
            (SELECT MAX(created_at) FROM predictions) as last_pred,
            (SELECT MAX(created_at) FROM signals) as last_sig
    """)
    times = cursor.fetchone()

    conn.close()

    return {
        "recent_predictions": recent_preds,
        "last_pred_time": times[0],
        "last_sig_time": times[1]
    }

def diagnose():
    print("=" * 70)
    print("META_002 深度診斷：prediction_id 追蹤問題")
    print("=" * 70)
    print()

    # 1. 檢查 predictions 是否有有效 ID
    print("【1. Predictions ID 有效性】")
    pred_info = check_predictions_have_ids()
    print(f"  預測總數： {pred_info['total']:,}")
    print(f"  ID 為 NULL：{pred_info['null_ids']:,}")
    if pred_info['sample_ids']:
        print(f"  示例 ID：")
        for sid, sym, price in pred_info['sample_ids']:
            print(f"    {sid}... | {sym} | {price:.4f}")
    print()

    # 2. 分析 main_loop.py 中的追蹤操作
    print("【2. main_loop.py 中 _pred_id_by_symbol 操作】")
    code_info = check_pred_id_tracking_in_code()
    if "error" in code_info:
        print(f"  ✗ {code_info['error']}")
    else:
        print(f"  存儲操作 (_pred_id_by_symbol[symbol] = ...)：{len(code_info['store_operations'])} 處")
        for lineno, ctx in code_info['store_operations']:
            print(f"    L{lineno}: {ctx[:120]}")
        print(f"  讀取操作 (_pred_id_by_symbol.get(...)）：{len(code_info['retrieve_operations'])} 處")
        for lineno, ctx in code_info['retrieve_operations']:
            print(f"    L{lineno}: {ctx[:120]}")
        print(f"  存在檢查 (in _pred_id_by_symbol)：{len(code_info['check_operations'])} 處")
    print()

    # 3. 追蹤 hook_on_prediction 返回值
    print("【3. 鉤子調用追蹤】")
    trace = trace_pred_hook_call()
    print(f"  最後預測時間： {trace['last_pred_time']}")
    print(f"  最後信號時間： {trace['last_sig_time']}")
    print(f"  最近預測：")
    for row in trace['recent_predictions']:
        pred_id, sym, price = row[0], row[1], row[2]
        print(f"    {str(pred_id)[:20]}... | {sym} | {price:.4f}")
    print()

    # 4. 結論與建議
    print("【4. 結論與修復方向】")
    print("  問題：signals.prediction_id 全為 NULL")
    print("  根因：hook_on_signal 被調用時，prediction_id 為 None")
    print()
    print("  可能原因：")
    print("    a) hook_on_prediction 調用失敗（exception 被靜默捕獲）")
    print("    b) _pred_id_by_symbol 在信號產生時尚未被填充")
    print("    c) symbol key 不匹配（大小寫/格式問題）")
    print()
    print("  驗證方法：")
    print("    → 檢查 v3_live.log 中 [SELFLEARN] pred_id= 記錄")
    print("    → 確認 hook_on_prediction 返回的 ID 是否為 None")
    print("    → 對比 predictions 和 signals 的 symbol 格式")
    print()
    print("=" * 70)

if __name__ == "__main__":
    diagnose()