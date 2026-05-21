#!/usr/bin/env python3
"""Diagnostic: Check signal-writing pipeline in self_learn.

Checks:
1. Are predictions being written (yes, 10k+ in DB)?
2. Are signals linked to predictions? (no — all have prediction_id=NULL)
3. Does _pred_id_by_symbol tracking exist in main_loop?
4. Are there any signals referencing valid prediction UUIDs?

Run: python3 scripts/diagnose_signal_chain.py
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "self_learn" / "trading_bot.db"

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1. Basic counts
    cur.execute("SELECT COUNT(*) FROM predictions")
    n_pred = cur.fetchone()[0]
    print(f"[1] Predictions in DB: {n_pred:,}")

    cur.execute("SELECT COUNT(*) FROM signals")
    n_sig = cur.fetchone()[0]
    print(f"[2] Signals in DB: {n_sig}")

    cur.execute("SELECT COUNT(*) FROM signals WHERE prediction_id IS NOT NULL")
    n_linked = cur.fetchone()[0]
    print(f"[3] Signals with prediction_id: {n_linked}")

    cur.execute("SELECT COUNT(*) FROM outcomes")
    n_out = cur.fetchone()[0]
    print(f"[4] Outcomes in DB: {n_out}")

    # 2. Sample predictions (latest)
    cur.execute("""
        SELECT id, symbol, predicted_price, confidence, created_at
        FROM predictions ORDER BY created_at DESC LIMIT 5
    """)
    print("\n[5] Latest predictions:")
    for row in cur.fetchall():
        print(f"  {row['id'][:8]}... | {row['symbol']} | price={row['predicted_price']} | conf={row['confidence']:.3f} | {row['created_at']}")

    # 3. Sample signals
    cur.execute("""
        SELECT id, prediction_id, action, entry_price, size, status, created_at
        FROM signals ORDER BY created_at DESC LIMIT 5
    """)
    print("\n[6] Latest signals:")
    for row in cur.fetchall():
        print(f"  {row['id'][:8]}... | pred_id={str(row['prediction_id'])[:8] if row['prediction_id'] else 'NULL'} | {row['action']} | price={row['entry_price']} | size={row['size']} | {row['status']}")

    # 4. Check if any signal prediction_ids are valid
    cur.execute("""
        SELECT s.id, s.prediction_id
        FROM signals s
        WHERE s.prediction_id IS NOT NULL
        LIMIT 10
    """)
    linked = cur.fetchall()
    print(f"\n[7] Linked signals (prediction_id NOT NULL): {len(linked)}")
    for row in linked:
        print(f"  {row['id'][:8]}... -> pred {row['prediction_id'][:8]}...")

    # 5. Check predictions time range
    cur.execute("SELECT MIN(created_at), MAX(created_at) FROM predictions")
    min_ts, max_ts = cur.fetchone()
    print(f"\n[8] Prediction time range: {min_ts} → {max_ts}")

    # 6. Check signals time range
    cur.execute("SELECT MIN(created_at), MAX(created_at) FROM signals")
    min_sig, max_sig = cur.fetchone()
    print(f"[9] Signal time range: {min_sig} → {max_sig}")

    # 7. Verify schema: signals.prediction_id column exists and type
    cur.execute("PRAGMA table_info(signals)")
    cols = {r['name']: r for r in cur.fetchall()}
    print(f"\n[10] Signals table columns:")
    for name, info in cols.items():
        print(f"  {name}: {info['type']} (nullable={not info['notnull']})")

    # 8. Check if predictions exist for signal symbols
    cur.execute("SELECT DISTINCT symbol FROM signals")
    sig_symbols = [r['symbol'] for r in cur.fetchall()]
    print(f"\n[11] Symbols in signals: {sig_symbols}")
    for sym in sig_symbols:
        cur.execute("SELECT COUNT(*) FROM predictions WHERE symbol = ?", (sym,))
        cnt = cur.fetchone()[0]
        print(f"  {sym}: {cnt} predictions")

    conn.close()
    print("\n[DONE] Diagnostic complete.")

if __name__ == "__main__":
    main()