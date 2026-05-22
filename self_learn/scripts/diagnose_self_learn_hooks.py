#!/usr/bin/env python3
"""
Diagnostic: Diagnose why hook_on_prediction is failing silently.

Checks:
1. Can self_learn module import successfully?
2. Is self_learn.config loaded?
3. Is the DB schema valid (prediction_error column in outcomes)?
4. Can hook_on_prediction run standalone?
5. Can hook_on_signal run standalone?
6. What is the exception type when self_learn hooks fail in main_loop context?

Run: python3 scripts/diagnose_self_learn_hooks.py
"""
import sys
import traceback
from pathlib import Path

# Add workspace to path (parent of self_learn/)
WS = Path(__file__).parent.parent.parent
sys.path.insert(0, str(WS))

def check_import():
    print("=" * 60)
    print("[1] Checking self_learn module imports...")
    try:
        from self_learn import hook_on_prediction, hook_on_signal
        print(f"  [OK] hook_on_prediction imported: {hook_on_prediction}")
        print(f"  [OK] hook_on_signal imported: {hook_on_signal}")
        return True
    except Exception as e:
        print(f"  [FAIL] Import error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False

def check_config():
    print("\n" + "=" * 60)
    print("[2] Checking self_learn.config...")
    try:
        from self_learn.config import RETRAIN_MIN_OUTCOMES, RETRAIN_INTERVAL_HOURS, MODEL_DIR
        print(f"  [OK] RETRAIN_MIN_OUTCOMES: {RETRAIN_MIN_OUTCOMES}")
        print(f"  [OK] RETRAIN_INTERVAL_HOURS: {RETRAIN_INTERVAL_HOURS}")
        print(f"  [OK] MODEL_DIR: {MODEL_DIR}")
        return True
    except Exception as e:
        print(f"  [FAIL] Config error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False

def check_db_schema():
    print("\n" + "=" * 60)
    print("[3] Checking DB schema (outcome column check)...")
    import sqlite3
    from self_learn.models import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(outcomes)")
        cols = {r[1]: r for r in cur.fetchall()}
        print(f"  DB_PATH: {DB_PATH}")
        print(f"  outcomes columns: {list(cols.keys())}")
        if 'prediction_error' in cols:
            print(f"  [OK] prediction_error column exists (type={cols['prediction_error'][2]})")
        else:
            print(f"  [FAIL] prediction_error column MISSING from outcomes table!")
        conn.close()
        return 'prediction_error' in cols
    except Exception as e:
        print(f"  [FAIL] DB schema error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False

def check_models_db():
    print("\n" + "=" * 60)
    print("[4] Checking models.py DB operations...")
    try:
        from self_learn.models import get_session, Prediction, Signal, Outcome
        from sqlalchemy import inspect
        
        session = get_session()
        inspector = inspect(session.bind)
        
        tables = inspector.get_table_names()
        print(f"  Tables in DB: {tables}")
        
        # Check if tables have correct columns
        for table in ['predictions', 'signals', 'outcomes']:
            if table in tables:
                cols = [c['name'] for c in inspector.get_columns(table)]
                print(f"  {table} columns: {cols}")
        
        session.close()
        return True
    except Exception as e:
        print(f"  [FAIL] models.py error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False

def check_hook_on_prediction():
    print("\n" + "=" * 60)
    print("[5] Testing hook_on_prediction standalone...")
    try:
        from self_learn import hook_on_prediction
        
        # Test with a fake prediction
        result = hook_on_prediction(
            symbol="TEST.DIAG",
            predicted_price=150.0,
            confidence=0.75,
            indicators={"RSI_14": 45.0, "MACD_HIST": 0.1},
        )
        print(f"  [OK] hook_on_prediction returned: {result[:8]}...")
        return True, result
    except Exception as e:
        print(f"  [FAIL] hook_on_prediction error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False, None

def check_hook_on_signal(prediction_id):
    print("\n" + "=" * 60)
    print("[6] Testing hook_on_signal standalone...")
    try:
        from self_learn import hook_on_signal
        
        result = hook_on_signal(
            action="BUY",
            prediction_id=prediction_id,
            entry_price=150.0,
            size=100,
        )
        print(f"  [OK] hook_on_signal returned: {result[:8]}...")
        return True, result
    except Exception as e:
        print(f"  [FAIL] hook_on_signal error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False, None

def check_main_loop_import():
    print("\n" + "=" * 60)
    print("[7] Checking if main_loop can import self_learn.hooks...")
    try:
        # Simulate what main_loop does
        from self_learn import hook_on_prediction, hook_on_signal
        print("  [OK] main_loop-style import successful")
        
        # Check if model_manager exists (for context)
        sys.path.insert(0, str(WS / "v3_pipeline"))
        try:
            from core.main_loop import LiveTradingLoop
            print("  [OK] LiveTradingLoop imported")
        except Exception as e:
            print(f"  [WARN] LiveTradingLoop import: {type(e).__name__}: {e}")
        
        return True
    except Exception as e:
        print(f"  [FAIL] main_loop import error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False

def main():
    print("=== Self-Learn Hook Diagnostics ===")
    print(f"Workspace: {WS}")
    
    results = {}
    
    # Run checks
    results['import'] = check_import()
    results['config'] = check_config()
    results['db_schema'] = check_db_schema()
    results['models'] = check_models_db()
    
    if results['import'] and results['db_schema']:
        hook_ok, pred_id = check_hook_on_prediction()
        results['hook_prediction'] = hook_ok
        if hook_ok and pred_id:
            sig_ok, sig_id = check_hook_on_signal(pred_id)
            results['hook_signal'] = sig_ok
    else:
        results['hook_prediction'] = False
        results['hook_signal'] = False
    
    results['main_loop_import'] = check_main_loop_import()
    
    # Summary
    print("\n" + "=" * 60)
    print("=== SUMMARY ===")
    for k, v in results.items():
        status = "✓" if v else "✗"
        print(f"  {status} {k}: {v}")
    
    # Determine root cause
    print("\n=== ROOT CAUSE ANALYSIS ===")
    if not results['import']:
        print("ISSUE: self_learn module cannot be imported.")
        print("  → Likely: self_learn/__init__.py is missing or broken")
    elif not results['db_schema']:
        print("ISSUE: outcomes table missing prediction_error column.")
        print("  → Action: Run DB migration to add prediction_error column")
    elif not results['hook_prediction']:
        print("ISSUE: hook_on_prediction throws exception in isolation.")
        print("  → Likely: DB write fails or models.py error")
    elif not results['main_loop_import']:
        print("ISSUE: main_loop cannot import self_learn hooks.")
        print("  → Likely: Import path conflict or circular import")
    else:
        print("All checks passed. Issue is likely:")
        print("  → hook_on_prediction silently failing inside main_loop try/except")
        print("  → or model_manager is None and exception is caught")

def cleanup_test_data():
    """Clean up any test data written during diagnostics."""
    import sqlite3
    from self_learn.models import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        # Delete test prediction/signal
        cur.execute("DELETE FROM predictions WHERE symbol='TEST.DIAG'")
        cur.execute("DELETE FROM signals WHERE action='BUY' AND entry_price=150.0 AND size=100")
        conn.commit()
        print("\n[Test data cleaned up]")
        conn.close()
    except Exception:
        pass

if __name__ == "__main__":
    main()
    cleanup_test_data()
    print("\n[DONE] Diagnostic complete.")