import json
import time

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - optional dependency for manual script
    requests = None

# 🔑 哥哥大人賜予的神聖鑰匙
API_KEY = "hHfrJP26kgrRpr_9rS3ykzexUCppb0wQ"
SYMBOL = "TSLA"

# 🛠️ 正確的Header格式
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json"
}

def try_endpoint(path, description):
    if requests is None:
        print("⚠️ requests not installed; skipping endpoint probe.")
        return False, None

    url = f"https://api.massive.com{path}"
    print(f"\n📡 Testing {description}...\nURL: {url}")
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            # 只顯示前於500個字元，避免洗版
            print(f"🎉 SUCCESS! Data snippet: {str(data)[:500]}")
            return True, data
        else:
            print(f"❌ Failed: {response.text[:200]}")
            return False, None
    except Exception as e:
        print(f"⚠️ Exception: {e}")
        return False, None

print(f"🚀 Massive Radar: Scanning for {SYMBOL}...")

# 1. 嘗試抓取上一日收盤 (通常最穩)
# 格式猜測: /v2/aggs/ticker/{ticker}/prev
try_endpoint(f"/v2/aggs/ticker/{SYMBOL}/prev", "Previous Close (v2)")

# 2. 嘗試抓取即時成交 (Trades)
# 格式參考: /v3/trades/{ticker}?limit=1
try_endpoint(f"/v3/trades/{SYMBOL}?limit=1", "Latest Trade (v3)")

# 3. 嘗試抓取快照 (Snapshot)
# 格式猜測: /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}
try_endpoint(f"/v2/snapshot/locale/us/markets/stocks/tickers/{SYMBOL}", "Market Snapshot (v2)")

print("\n✅ Scan Complete.")
