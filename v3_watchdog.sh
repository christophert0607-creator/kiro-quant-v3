#!/usr/bin/env bash
# ==========================================
# V3.6 Watchdog Script
# Automatically restarts v3_launcher.py if it crashes
# Usage: nohub ./v3_watchdog.sh &
# ==========================================

SCRIPT_TO_WATCH="v3_launcher.py"
SLEEP_TIME=10
CRASH_COOLDOWN=5

# Function to send Telegram alert using Python
send_alert() {
    local message="$1"
    python3 -c "import sys; sys.path.append('.'); from notifier import send_tg_msg; send_tg_msg('[Watchdog] ' + sys.argv[1])" "$message" || echo "Failed to send TG alert"
}

echo "Starting V3 Watchdog for $SCRIPT_TO_WATCH..."
send_alert "🟢 Watchdog started for $SCRIPT_TO_WATCH"

while true; do
    # Check if the process is running
    if ! pgrep -f "python3 $SCRIPT_TO_WATCH" > /dev/null; then
        echo "🚨 $SCRIPT_TO_WATCH is not running! Starting it now..."
        send_alert "🔴 $SCRIPT_TO_WATCH crashed or stopped! Restarting now..."
        
        # Start the script in the background
        nohup python3 $SCRIPT_TO_WATCH --profile lite >> v3_launcher_watchdog.log 2>&1 &
        
        # Wait a bit after restart to prevent rapid crash loops
        sleep $CRASH_COOLDOWN
    fi
    
    # Sleep before next check
    sleep $SLEEP_TIME
done
