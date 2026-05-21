#!/usr/bin/env python3
"""
meta_002 — 信號鏈接診斷腳本

目標：確認 LiveTradingLoop 的 hook_on_signal 是否正確接收 prediction_id
問題：trading_bot.db 有 10606 predictions 但 signals.prediction_id 全為 NULL

診斷方法：
1. 檢查 signals 表中 prediction_id 為 NULL vs 非 NULL 的比例
2. 確認 hook_on_signal 的調用方式（main_loop.py 是否正確傳遞 pred_id）
3. 對比 predictions 表最後寫入時間 vs signals 表最後寫入時間

用法：
    cd /home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3
    python3 dev/meta_labeling/scripts/diagnose_signal_chain.py
"""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/self_learn/trading_bot.db")
MAIN_LOOP = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/v3_pipeline/core/main_loop.py")

def get_db_stats():
    """檢查信號鏈接狀態"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # 預測總數
    cursor.execute("SELECT COUNT(*) FROM predictions")
    total_preds = cursor.fetchone()[0]

    # 信號總數
    cursor.execute("SELECT COUNT(*) FROM signals")
    total_signals = cursor.fetchone()[0]

    # 有 prediction_id 的信號數
    cursor.execute("SELECT COUNT(*) FROM signals WHERE prediction_id IS NOT NULL")
    linked_signals = cursor.fetchone()[0]

    # 無 prediction_id 的信號數
    unlinked_signals = total_signals - linked_signals

    # 最後預測時間
    cursor.execute("SELECT MAX(created_at) FROM predictions")
    last_pred = cursor.fetchone()[0]

    # 最後信號時間
    cursor.execute("SELECT MAX(created_at) FROM signals")
    last_sig = cursor.fetchone()[0]

    # 測試鉤子是否正常：寫入一條測試信號
    test_sig_id = "test_diag_001"
    cursor.execute("""
        INSERT INTO signals (id, action, prediction_id, entry_price, size, status, created_at)
        VALUES (?, 'BUY', NULL, 999.0, 1, 'OPEN', ?)
    """, (test_sig_id, datetime.now(timezone.utc).isoformat()))
    conn.commit()

    # 立即讀回確認寫入成功
    cursor.execute("SELECT id, action, entry_price FROM signals WHERE id = ?", (test_sig_id,))
    verify = cursor.fetchone()
    write_ok = verify is not None

    # 清理測試記錄
    cursor.execute("DELETE FROM signals WHERE id = ?", (test_sig_id,))
    conn.commit()

    conn.close()

    return {
        "total_predictions": total_preds,
        "total_signals": total_signals,
        "linked_signals": linked_signals,
        "unlinked_signals": unlinked_signals,
        "last_prediction_at": last_pred,
        "last_signal_at": last_sig,
        "write_ok": write_ok,
    }

def check_main_loop_hook():
    """檢查 main_loop.py 中 hook_on_signal 的調用方式"""
    if not MAIN_LOOP.exists():
        return {"found": False, "error": "main_loop.py not found"}

    content = MAIN_LOOP.read_text()

    # 找所有 hook_on_signal 調用
    import re
    pattern = r'hook_on_signal\s*\([^)]+\)'
    matches = re.findall(pattern, content, re.DOTALL)

    return {
        "found": True,
        "call_count": len(matches),
        "calls": [m.strip()[:200] for m in matches[:5]],  # 最多5條
    }

def diagnose():
    print("=" * 60)
    print("META_002 診斷：信號鏈接問題")
    print("=" * 60)
    print()

    # DB 狀態
    print("【資料庫狀態】")
    stats = get_db_stats()
    print(f"  預測總數：        {stats['total_predictions']:,}")
    print(f"  信號總數：        {stats['total_signals']:,}")
    print(f"  有鏈接預測：      {stats['linked_signals']:,}")
    print(f"  無鏈接預測：      {stats['unlinked_signals']:,}")
    print(f"  最後預測時間：    {stats['last_prediction_at']}")
    print(f"  最後信號時間：    {stats['last_signal_at']}")
    print(f"  寫入測試：        {'✓ 正常' if stats['write_ok'] else '✗ 失敗'}")
    print()

    # main_loop.py 鉤子調用
    print("【main_loop.py 中 hook_on_signal 調用】")
    hook_info = check_main_loop_hook()
    if hook_info["found"]:
        print(f"  找到 {hook_info['call_count']} 處調用：")
        for i, call in enumerate(hook_info["calls"], 1):
            print(f"    [{i}] {call[:150]}...")
    else:
        print(f"  ✗ {hook_info.get('error', 'unknown error')}")
    print()

    # 結論
    print("【結論】")
    if stats["linked_signals"] == 0 and stats["total_signals"] > 0:
        print("  ⚠ 所有信號均無 prediction_id 鏈接")
        print("  → 問題在 main_loop.py 的 hook_on_signal 調用")
        print("  → 需要確認 prediction_id 是否正確傳遞")
    elif stats["total_signals"] == 0:
        print("  ⚠ 信號錶為空，LiveTradingLoop 可能未產生交易")
    else:
        pct = stats["linked_signals"] / stats["total_signals"] * 100
        print(f"  ✓ {pct:.1f}% 信號有預測鏈接（正常）")

    print()
    print("=" * 60)

if __name__ == "__main__":
    diagnose()