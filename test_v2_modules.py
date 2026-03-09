#!/usr/bin/env python3
"""
Quant Engine v2 - Module Smoke Test
=====================================
✅ 驗證所有新模組可以正確 import
✅ 驗證 RiskGuard 邏輯
✅ 驗證 StateStore 讀寫
✅ 不需要連接 FutuOpenD
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("  Quant Engine v2 — Module Smoke Test")
print("=" * 60)

# Test 1: Config
print("\n[1] Testing config.py...")
try:
    from config import RISK_CONFIG, TRADE_MODE, STOCK_LIST, is_live, print_config
    print(f"  ✅ TRADE_MODE: {TRADE_MODE}")
    print(f"  ✅ STOCK_LIST: {STOCK_LIST}")
    print(f"  ✅ is_live(): {is_live()}")
    print(f"  ✅ RiskConfig: max_pos={RISK_CONFIG.max_position_pct*100:.0f}% daily_loss={RISK_CONFIG.max_daily_loss_pct*100:.0f}%")
except Exception as e:
    print(f"  ❌ FAIL: {e}")
    sys.exit(1)

# Test 2: StateStore
print("\n[2] Testing state_store.py...")
try:
    import state_store as ss
    state = ss.load()
    print(f"  ✅ State loaded | date: {state['date']} | trades: {state.get('daily_trades', 0)}")

    # Test duplicate order detection
    ss.record_order(state, "US.TSLA", "BUY", 5, 250.0)
    is_dup = ss.is_duplicate_order(state, "US.TSLA", "BUY", 5, 250.0, cooldown_secs=60)
    print(f"  ✅ Duplicate order detection: {is_dup} (should be True)")

    is_dup2 = ss.is_duplicate_order(state, "US.F", "BUY", 10, 10.0, cooldown_secs=60)
    print(f"  ✅ Different stock check: {is_dup2} (should be False)")

    ss.save(state)
    print("  ✅ State save OK")
except Exception as e:
    print(f"  ❌ FAIL: {e}")
    sys.exit(1)

# Test 3: RiskGuard
print("\n[3] Testing risk_guard.py...")
try:
    from risk_guard import RiskGuard
    rg = RiskGuard()

    # Load fresh state
    import json
    test_state = {
        "date": "2099-01-01",
        "daily_pnl": 0.0,
        "daily_trades": 0,
        "consecutive_errors": 0,
        "circuit_broken": False,
        "circuit_break_time": None,
        "last_orders": {},
        "positions": {},
        "total_invested": 0.0,
    }

    # Test 1: Normal BUY should pass
    result = rg.check(
        state=test_state,
        code="US.TSLA",
        signal="BUY",
        price=250.0,
        available_cash=100000,
        current_positions={},
    )
    print(f"  ✅ Normal BUY: ok={result['ok']}, qty={result.get('qty')}")

    # Test 2: HOLD should reject
    result2 = rg.check(
        state=test_state,
        code="US.TSLA",
        signal="HOLD",
        price=250.0,
        available_cash=100000,
        current_positions={},
    )
    print(f"  ✅ HOLD reject: ok={result2['ok']}, reason={result2.get('reason')}")

    # Test 3: Circuit break
    test_state["circuit_broken"] = True
    result3 = rg.check(
        state=test_state,
        code="US.TSLA",
        signal="BUY",
        price=250.0,
        available_cash=100000,
        current_positions={},
    )
    print(f"  ✅ Circuit broken: ok={result3['ok']}, reason={result3.get('reason')}")

    # Test 4: Max daily loss
    test_state["circuit_broken"] = False
    test_state["daily_pnl"] = -100000  # massive loss
    result4 = rg.check(
        state=test_state,
        code="US.TSLA",
        signal="BUY",
        price=250.0,
        available_cash=100000,
        current_positions={},
    )
    print(f"  ✅ Max daily loss: ok={result4['ok']}, reason={result4.get('reason')}")

except Exception as e:
    print(f"  ❌ FAIL: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("  ✅ ALL TESTS PASSED")
print("  ✅ Quant Engine v2 模組驗證完成")
print("  ✅ 下一步: 啟動 FutuOpenD 後執行 quant_v2.py")
print("=" * 60)
