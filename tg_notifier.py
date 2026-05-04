import os
import requests
import logging

TOKEN = os.environ.get("FUTU_TG_TOKEN", "")
CHAT_ID = os.environ.get("FUTU_TG_CHAT_ID", "")

def send_tg_message(text):
    if not TOKEN or not CHAT_ID:
        logging.warning("Telegram not configured (FUTU_TG_TOKEN / FUTU_TG_CHAT_ID missing)")
        return None
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json()
    except Exception as e:
        logging.error(f"Telegram Send Error: {e}")
        return None

if __name__ == "__main__":
    send_tg_message("🚀 *Dev-Droid 系統覺醒！*\n量化交易監控已成功對接 Telegram。")
