import futu as ft
import json

def trade_f():
    # 使用模擬環境進行測試
    trd_ctx = ft.OpenUSTradeContext(host="127.0.0.1", port=11111)
    
    # 解鎖交易
    ret, data = trd_ctx.unlock_trade("930607")
    if ret != ft.RET_OK:
        print(json.dumps({"error": f"Unlock failed: {data}"}))
        return

    # 執行買入指令：買入 1 股 福特汽車 (F)
    # 使用市價單 (Market Order)
    ret, data = trd_ctx.place_order(
        qty=1, 
        price=0, 
        code="US.F", 
        trd_side=ft.TrdSide.BUY, 
        order_type=ft.OrderType.MARKET, 
        trd_env=ft.TrdEnv.SIMULATE
    )
    
    result = {
        "ret": "OK" if ret == ft.RET_OK else "ERROR",
        "data": data.to_dict(orient='records') if ret == ft.RET_OK else str(data)
    }
    
    trd_ctx.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    trade_f()
