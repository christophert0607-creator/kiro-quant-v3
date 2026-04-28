import requests
import json

API_KEY = "hHfrJP26kgrRpr_9rS3ykzexUCppb0wQ"
SYMBOL = "TSLA"

def try_url(url, description):
    print(f"Testing {description}: {url}")
    try:
        response = requests.get(url, timeout=5)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            print("🎉 SUCCESS!")
            print(json.dumps(response.json(), indent=2))
            return True
        else:
            print(f"❌ Failed: {response.text[:200]}")
    except Exception as e:
        print(f"⚠️ Error: {e}")
    return False

# Common API patterns based on modern REST standards
patterns = [
    # Massive.com specific guesses
    f"https://api.massive.com/v1/stocks/{SYMBOL}/quote?api_key={API_KEY}",
    f"https://api.massive.com/stocks/{SYMBOL}/quote?api_key={API_KEY}",
    f"https://api.massive.com/v1/tickers/{SYMBOL}/quote?api_key={API_KEY}",
    
    # Try their 'market-snapshot' style mentioned in search results
    f"https://api.massive.com/v1/stocks/snapshot?symbols={SYMBOL}&api_key={API_KEY}",
    
    # Try a simple daily summary
    f"https://api.massive.com/v1/stocks/{SYMBOL}/daily?api_key={API_KEY}"
]

print("🚀 Starting Massive API Recon Mission...")
for url in patterns:
    if try_url(url, "Endpoint Pattern"):
        break
