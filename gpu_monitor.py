import subprocess
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("v3_pipeline/logs/gpu_monitor.log"), 
        logging.StreamHandler()
    ]
)

import os
os.makedirs("v3_pipeline/logs", exist_ok=True)

def get_gpu_stats():
    try:
        cmd = ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.memory,memory.total,memory.free,memory.used,temperature.gpu", "--format=csv,noheader,nounits"]
        output = subprocess.check_output(cmd).decode('utf-8').strip().split(', ')
        return {
            "gpu_util": float(output[0]),
            "mem_util": float(output[1]),
            "mem_total": float(output[2]),
            "mem_free": float(output[3]),
            "mem_used": float(output[4]),
            "temp": float(output[5])
        }
    except Exception as e:
        logging.error(f"Error reading GPU stats: {e}")
        return None

def main():
    logging.info("🚀 Starting 24/7 GPU Monitor...")
    while True:
        stats = get_gpu_stats()
        if stats:
            mem_percent = (stats["mem_used"] / stats["mem_total"]) * 100
            status_msg = f"GPU: {stats['gpu_util']}% | Mem: {stats['mem_used']}MB/{stats['mem_total']}MB ({mem_percent:.1f}%) | Temp: {stats['temp']}°C"
            logging.info(status_msg)
            
            if stats["temp"] > 85:
                logging.warning(f"⚠️ HIGH TEMPERATURE ALERT: {stats['temp']}°C")
            if mem_percent > 90:
                logging.warning(f"⚠️ HIGH VRAM USAGE ALERT: {mem_percent:.1f}%")
        
        time.sleep(10) # 10s poll rate

if __name__ == "__main__":
    main()
