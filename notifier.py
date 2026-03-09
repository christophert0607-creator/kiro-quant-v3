import requests
import logging
from config import TG_TOKEN, TG_CHAT_ID, TG_ENABLED, TRADE_MODE

log = logging.getLogger("Notifier")

def send_tg_msg(message: str):
    if not TG_ENABLED:
        return
    
    # 增加模式標籤
    tag = "🔴 [LIVE] " if TRADE_MODE == "LIVE" else "🟡 [SIM] "
    full_msg = f"{tag}{message}"
    
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": full_msg,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            log.error(f"❌ TG 通知失敗: {response.text}")
    except Exception as e:
        log.error(f"❌ TG 連線例外: {e}")

if __name__ == "__main__":
    # 測試執行
    print("發送測試訊息中...")
    send_tg_msg("🚀 *小祈量化守護者* 已上線！\n連線正確，正在監控市場。")
