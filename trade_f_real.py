import futu as ft
import json

def trade_f_real():
    # 小祈準備衝鋒實盤戰場！
    trd_ctx = ft.OpenUSTradeContext(host="127.0.0.1", port=11111)
    
    # 這裡需要解鎖交易權限喔，歐尼醬！
    ret, data = trd_ctx.unlock_trade("930607")
    if ret != ft.RET_OK:
        print(json.dumps({"error": f"Unlock failed: {data}"}))
        return

    # 🚀 實盤買入指令：買入 1 股 福特汽車 (F)
    # 環境設定為 TrdEnv.REAL！
    ret, data = trd_ctx.place_order(
        qty=1, 
        price=0, 
        code="US.F", 
        trd_side=ft.TrdSide.BUY, 
        order_type=ft.OrderType.MARKET, 
        trd_env=ft.TrdEnv.REAL
    )
    
    result = {
        "ret": "OK" if ret == ft.RET_OK else "ERROR",
        "data": data.to_dict(orient='records') if ret == ft.RET_OK else str(data)
    }
    
    trd_ctx.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    trade_f_real()
