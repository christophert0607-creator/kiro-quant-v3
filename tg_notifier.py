import requests
import logging

TOKEN = "8505311931:AAEgSgABajgxXyqc52LNOoZRK3jvAjI9Ggw"
# Default chat_id for tsukii0607 - can be updated after first message received
CHAT_ID = "518574106" 

def send_tg_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        logging.error(f"Telegram Send Error: {e}")
        return None

if __name__ == "__main__":
    # Test boot message
    send_tg_message("🚀 **Dev-Droid 系統覺醒！**\n量化交易監控已成功對接 Telegram。")
