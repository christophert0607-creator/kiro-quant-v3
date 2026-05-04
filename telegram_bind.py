#!/usr/bin/env python3
"""
Telegram 綁定工具 - 自動偵測 chat_id 並寫入 .env
用法: python telegram_bind.py
"""

import os
import sys
import time
import requests

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

POLL_TIMEOUT = 60   # 等待 /start 訊息的秒數
POLL_INTERVAL = 2   # 每次輪詢間隔


def _load_env() -> dict:
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
    return env


def _write_env(env: dict):
    lines = [
        "# Futu OpenD",
        f"FUTU_OPEND_HOST={env.get('FUTU_OPEND_HOST', '')}",
        f"FUTU_OPEND_PORT={env.get('FUTU_OPEND_PORT', '11111')}",
        "",
        "# Trading password",
        f"TRADING_PASSWORD={env.get('TRADING_PASSWORD', '')}",
        f"FUTU_TRADE_PWD={env.get('FUTU_TRADE_PWD', '')}",
        "",
        "# Telegram",
        f"FUTU_TG_TOKEN={env.get('FUTU_TG_TOKEN', '')}",
        f"FUTU_TG_CHAT_ID={env.get('FUTU_TG_CHAT_ID', '')}",
        f"FUTU_TG_ENABLED={env.get('FUTU_TG_ENABLED', '1')}",
    ]
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _api(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = requests.get(url, params=params, timeout=10)
    return r.json()


def _get_token() -> str:
    env = _load_env()
    existing = env.get("FUTU_TG_TOKEN", "")
    if existing:
        print(f"已有 Token: {existing[:20]}...")
        keep = input("使用現有 Token? [Y/n]: ").strip().lower()
        if keep != "n":
            return existing
    token = input("請輸入 Bot Token (從 @BotFather 取得): ").strip()
    return token


def _poll_chat_id(token: str) -> str:
    """輪詢 getUpdates，等待用戶對 bot 發送任意訊息後返回 chat_id。"""
    print(f"\n請在 Telegram 中找到你的 bot 並傳送 /start 或任何訊息...")
    print(f"(等待 {POLL_TIMEOUT} 秒)\n")

    # 取得當前最新 update_id，避免處理舊訊息
    result = _api(token, "getUpdates", limit=1, offset=-1, timeout=0)
    last_id = 0
    if result.get("ok") and result["result"]:
        last_id = result["result"][-1]["update_id"]

    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        result = _api(token, "getUpdates", limit=10, offset=last_id + 1, timeout=5)
        if result.get("ok"):
            for update in result["result"]:
                last_id = update["update_id"]
                msg = update.get("message") or update.get("channel_post")
                if msg:
                    chat_id = str(msg["chat"]["id"])
                    sender = (
                        msg["chat"].get("username")
                        or msg["chat"].get("first_name")
                        or chat_id
                    )
                    print(f"偵測到訊息來自: {sender}  (chat_id={chat_id})")
                    return chat_id
        time.sleep(POLL_INTERVAL)

    return ""


def _send_test(token: str, chat_id: str):
    result = _api(
        token,
        "sendMessage",
        chat_id=chat_id,
        text="✅ *Kiro Quant 綁定成功！*\n量化交易系統已與此 Telegram 帳號綁定，交易通知將發送至此。",
        parse_mode="Markdown",
    )
    if result.get("ok"):
        print("測試訊息已發送！")
    else:
        print(f"發送失敗: {result.get('description', '未知錯誤')}")


def main():
    print("=" * 50)
    print("  Kiro Quant — Telegram 綁定工具")
    print("=" * 50)

    # 1. 取得 Token
    token = _get_token()
    if not token:
        print("❌ 未輸入 Token，退出。")
        sys.exit(1)

    # 驗證 token
    me = _api(token, "getMe")
    if not me.get("ok"):
        print(f"❌ Token 無效: {me.get('description', '')}")
        sys.exit(1)
    bot_name = me["result"].get("username", "unknown")
    print(f"✅ Bot 驗證成功: @{bot_name}")

    # 2. 取得 chat_id
    env = _load_env()
    existing_cid = env.get("FUTU_TG_CHAT_ID", "")
    if existing_cid:
        print(f"已有 Chat ID: {existing_cid}")
        reuse = input("使用現有 Chat ID? [Y/n]: ").strip().lower()
        if reuse != "n":
            chat_id = existing_cid
        else:
            chat_id = _poll_chat_id(token)
    else:
        chat_id = _poll_chat_id(token)

    if not chat_id:
        chat_id = input("未偵測到訊息，請手動輸入 Chat ID: ").strip()

    if not chat_id:
        print("❌ 未取得 Chat ID，退出。")
        sys.exit(1)

    # 3. 寫入 .env
    env["FUTU_TG_TOKEN"] = token
    env["FUTU_TG_CHAT_ID"] = chat_id
    env["FUTU_TG_ENABLED"] = "1"
    _write_env(env)
    print(f"\n✅ 已寫入 .env")
    print(f"   FUTU_TG_TOKEN  = {token[:20]}...")
    print(f"   FUTU_TG_CHAT_ID = {chat_id}")

    # 4. 發送測試訊息
    send = input("\n發送測試訊息確認綁定? [Y/n]: ").strip().lower()
    if send != "n":
        _send_test(token, chat_id)

    print("\n綁定完成！重新啟動交易系統後 Telegram 通知即生效。")


if __name__ == "__main__":
    main()
