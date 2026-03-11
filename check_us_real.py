import futu as ft
import pandas as pd
import json

def get_snapshot():
    # 這裡小祈會使用 OpenUSTradeContext 是專門對應美股市場的喔！
    trd_ctx = ft.OpenUSTradeContext(host="127.0.0.1", port=11111)
    ret, data = trd_ctx.unlock_trade("930607")
    
    results = {}
    
    # 專注於美股實盤市場！
    # 如果要看港股，小祈下次會用 OpenHKTradeContext 噠！
    ret, acc_info_real = trd_ctx.accinfo_query(trd_env=ft.TrdEnv.REAL)
    if ret == ft.RET_OK:
        results["real_cash"] = float(acc_info_real.iloc[0]['cash'])
        results["real_total"] = float(acc_info_real.iloc[0]['total_assets'])
        results["real_market_val"] = float(acc_info_real.iloc[0]['market_val'])
    
    ret, pos_list_real = trd_ctx.position_list_query(trd_env=ft.TrdEnv.REAL)
    if ret == ft.RET_OK and not pos_list_real.empty:
        results["real_positions"] = pos_list_real.to_dict(orient='records')
    else:
        results["real_positions"] = []

    trd_ctx.close()
    print(json.dumps(results, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    get_snapshot()
