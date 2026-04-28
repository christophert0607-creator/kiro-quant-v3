# Test script to run futu_api quote 10 times
# This simulates a high-frequency polling scenario
import subprocess
import time
import json

CMD = ["/home/tsukii0607/.openclaw/workspace/skills/futu-api/venv/bin/python3", 
       "/home/tsukii0607/.openclaw/workspace/skills/futu-api/futu_api.py", 
       "quote", 
       "--symbol", "TSLA"]

print("🚀 Starting 10-Round Rapid Fire Test...")

results = []
for i in range(1, 11):
    start_time = time.time()
    try:
        # Run the command
        result = subprocess.run(CMD, capture_output=True, text=True, timeout=15)
        duration = time.time() - start_time
        
        status = "✅ OK" if result.returncode == 0 and "price" in result.stdout else "❌ FAIL"
        source = "Unknown"
        
        if result.returncode == 0:
            try:
                # Filter stdout to find the JSON line
                json_line = None
                for line in result.stdout.splitlines():
                    if line.strip().startswith("{") and line.strip().endswith("}"):
                        json_line = line.strip()
                        break
                
                if json_line:
                    data = json.loads(json_line)
                    source = data.get('source', 'unknown')
                    price = data.get('price', 'N/A')
                    print(f"Round {i:02d}: ✅ OK | Price: {price} | Source: {source} | Time: {duration:.2f}s")
                else:
                    print(f"Round {i:02d}: ⚠️ No JSON Found | Output: {result.stdout[:50]}...")
            except:
                print(f"Round {i:02d}: ⚠️ JSON Parse Error")
        else:
            print(f"Round {i:02d}: {status} | Error: {result.stderr[:50]}...")
            
    except Exception as e:
        print(f"Round {i:02d}: 💥 Exception: {e}")
    
    # 稍微休息一下，避免被 Massive 的免費額度擋下來 (Rate limiting)
    time.sleep(1)

print("\n🏁 Test Complete.")
