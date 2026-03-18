import os
import shutil
import glob

base_dir = "/home/tsukii0607/.openclaw/workspace/skills/kiro-quant"
archive_dir = os.path.join(base_dir, "legacy_archive")
scripts_dir = os.path.join(archive_dir, "v2_scripts")
logs_dir = os.path.join(archive_dir, "old_logs")

os.makedirs(scripts_dir, exist_ok=True)
os.makedirs(logs_dir, exist_ok=True)

legacy_scripts = [
    "quant_v2.py",
    "quant_system.py",
    "feature_engineering.py",
    "risk_guard.py",
    "test_v2_modules.py",
    "test_sliding_window.py",
    "real_sell_f.py",
    "test_sell_f.py",
    "test_sell_f_market.py",
    "trade_f_real.py",
    "trade_f_test.py",
    "cleanup_v2.py"
]

for script in legacy_scripts:
    src = os.path.join(base_dir, script)
    if os.path.exists(src):
        try:
            shutil.move(src, os.path.join(scripts_dir, script))
            print(f"Moved {script} to legacy_archive/v2_scripts/")
        except Exception as e:
            print(f"Error moving {script}: {e}")

# Move log files to declutter root
for log_ext in ["*.log", "*.log.gz"]:
    for log_file in glob.glob(os.path.join(base_dir, log_ext)):
        filename = os.path.basename(log_file)
        try:
            shutil.move(log_file, os.path.join(logs_dir, filename))
            print(f"Archived log {filename}")
        except Exception as e:
            print(f"Error moving {filename}: {e}")

print("Cleanup complete!")
