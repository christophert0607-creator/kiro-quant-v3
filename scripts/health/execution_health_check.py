
import json
import subprocess
from datetime import datetime, timedelta
from futu import *

# Configuration
CASH_ACC_ID = 18526451 

def check_execution_health():
    ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.NONE, host='127.0.0.1', port=11112, security_firm=SecurityFirm.FUTUSECURITIES)
    health = {
        "status": "HEALTHY",
        "issues": [],
        "metrics": {
            "pending_orders": 0,
            "zombie_orders": 0,
            "locked_positions": 0
        }
    }
    
    try:
        # 1. Check Pending Orders
        ret_ord, orders = ctx.order_list_query(trd_env=TrdEnv.SIMULATE, acc_id=CASH_ACC_ID)
        if ret_ord == RET_OK:
            health["metrics"]["pending_orders"] = len(orders)
            now = datetime.now()
            zombies = 0
            for _, row in orders.iterrows():
                if row['order_status'] == 'SUBMITTED':
                    try:
                        create_time = datetime.strptime(str(row['create_time']), '%Y-%m-%d %H:%M:%S')
                        if (now - create_time) > timedelta(hours=1):
                            zombies += 1
                    except:
                        pass
            health["metrics"]["zombie_orders"] = zombies
            if zombies > 0:
                health["status"] = "UNHEALTHY"
                health["issues"].append(f"Found {zombies} zombie orders (SUBMITTED > 1h).")
        
        # 2. Check Locked Positions
        ret_pos, pos = ctx.position_list_query(trd_env=TrdEnv.SIMULATE, acc_id=CASH_ACC_ID)
        if ret_pos == RET_OK:
            locked = 0
            for _, row in pos.iterrows():
                if row['qty'] > 0 and row['can_sell_qty'] == 0:
                    locked += 1
            health["metrics"]["locked_positions"] = locked
            if locked > 0:
                health["status"] = "UNHEALTHY"
                health["issues"].append(f"Found {locked} locked positions (qty > 0 but can_sell_qty == 0).")

    except Exception as e:
        health["status"] = "ERROR"
        health["issues"].append(f"Health check exception: {str(e)}")
    finally:
        ctx.close()
        
    return health

if __name__ == "__main__":
    report = check_execution_health()
    print(json.dumps(report, indent=2))
