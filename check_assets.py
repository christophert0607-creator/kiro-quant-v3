import futu as ft
import pandas as pd
import json

def get_snapshot():
    trd_ctx = ft.OpenUSTradeContext(host="127.0.0.1", port=11111)
    ret, data = trd_ctx.unlock_trade("930607")
    
    results = {}
    
    # 1. Check Simulate
    ret, acc_info = trd_ctx.accinfo_query(trd_env=ft.TrdEnv.SIMULATE)
    if ret == ft.RET_OK:
        results["simulate_cash"] = float(acc_info.iloc[0]['cash'])
        results["simulate_total"] = float(acc_info.iloc[0]['total_assets'])
    
    ret, pos_list = trd_ctx.position_list_query(trd_env=ft.TrdEnv.SIMULATE)
    if ret == ft.RET_OK and not pos_list.empty:
        results["simulate_positions"] = pos_list.to_dict(orient='records')
    else:
        results["simulate_positions"] = []

    # 2. Check Real
    ret, acc_info_real = trd_ctx.accinfo_query(trd_env=ft.TrdEnv.REAL)
    if ret == ft.RET_OK:
        results["real_cash"] = float(acc_info_real.iloc[0]['cash'])
        results["real_total"] = float(acc_info_real.iloc[0]['total_assets'])
    
    ret, pos_list_real = trd_ctx.position_list_query(trd_env=ft.TrdEnv.REAL)
    if ret == ft.RET_OK and not pos_list_real.empty:
        results["real_positions"] = pos_list_real.to_dict(orient='records')
    else:
        results["real_positions"] = []

    trd_ctx.close()
    print(json.dumps(results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    get_snapshot()
