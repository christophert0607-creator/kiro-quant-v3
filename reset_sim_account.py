import futu as ft
import json
import os

def reset_sim_account():
    # 建立交易上下文
    trd_ctx = ft.OpenUSTradeContext(host="127.0.0.1", port=11111)
    
    # 歐尼醬的解鎖咒語
    ret, data = trd_ctx.unlock_trade("930607")
    if ret != ft.RET_OK:
        print(f"❌ Unlock failed: {data}")
        return

    print("🧹 小祈正在執行模擬倉大掃除...")

    # 1. 查詢所有持倉
    ret, pos_list = trd_ctx.position_list_query(trd_env=ft.TrdEnv.SIMULATE)
    if ret == ft.RET_OK and not pos_list.empty:
        for index, row in pos_list.iterrows():
            code = row['code']
            qty = row['can_sell_qty']
            if qty > 0:
                print(f"🚀 正在清空持倉: {code}, 數量: {qty}")
                # 以市價單全數賣出
                trd_ctx.place_order(
                    qty=qty, 
                    price=0, 
                    code=code, 
                    trd_side=ft.TrdSide.SELL, 
                    order_type=ft.OrderType.MARKET, 
                    trd_env=ft.TrdEnv.SIMULATE
                )
    else:
        print("✨ 模擬倉目前沒有持倉，不需要清理喔！")

    # 2. 清理本地舊數據 (V3 戰略路徑)
    storage_path = "/home/tsukii0607/.openclaw/workspace/skills/futu-api/v3_pipeline/data/storage"
    if os.path.exists(storage_path):
        print(f"🗑️ 正在清理本地數據庫: {storage_path}")
        for f in os.listdir(storage_path):
            if f.endswith(('.csv', '.parquet')):
                os.remove(os.path.join(storage_path, f))

    trd_ctx.close()
    print("✅ 模擬倉重置完成！我們可以重新出發啦！噠噠噠！")

if __name__ == "__main__":
    reset_sim_account()
