from pathlib import Path
import pandas as pd

data_dir = Path("/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/data/hsi_training")
files = list(data_dir.glob("*.parquet"))

results = []
for f in files:
    try:
        df = pd.read_parquet(f)
        results.append({"ticker": f.name, "rows": len(df)})
    except Exception as e:
        results.append({"ticker": f.name, "rows": -1, "error": str(e)})

# 排序，看看最少的有多少天
results.sort(key=lambda x: x['rows'])

if not results:
    print("No data found!")
    exit()

min_rows = results[0]['rows']
max_rows = results[-1]['rows']
avg_rows = sum(r['rows'] for r in results if r['rows'] > 0) / len([r for r in results if r['rows'] > 0])

print(f"Total Stocks: {len(results)}")
print(f"Minimum Data: {min_rows} days")
print(f"Maximum Data: {max_rows} days")
print(f"Average Data: {avg_rows:.1f} days")
print("\nBottom 5 (Stocks with least data):")
for r in results[:5]:
    print(f"{r['ticker']}: {r['rows']} days")
