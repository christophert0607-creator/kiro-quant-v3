import json
import os
from datetime import datetime

config_path = "/home/tsukii0607/.openclaw/workspace-quant/kiro-quant-v3/config.sim.json"

# Current Market Context (Simulated logic based on pulse)
# 2800.HK: +0.77% (2d), -0.15% (30m) -> Stability
# 3033.HK: +0.42% (2d), -0.46% (30m) -> Tech weakness
# 9988.HK: +2.85% (2d) -> Outperformer
# Posture: Neutral (Cautious due to short-term Tech momentum -0.46%)

new_posture = "neutral"
rationale = "HSI (2800.HK) 持穩但 HSTECH (3033.HK) 30m 動量轉負 (-0.46%)。ATMXJ 中 9988.HK 雖強勢但 0700/3690 受壓。維持 Neutral 以平衡風險。下調 9988.HK 門檻以捕捉強勢回調，略升 0700/3690 門檻以防守。"

def clamp(val, min_v, max_v):
    return max(min_v, min(max_v, val))

with open(config_path, 'r') as f:
    config = json.load(f)

old_config = json.loads(json.dumps(config)) # deep copy for diff

# 1. Update Posture
config["posture"] = new_posture
config["posture_rationale"] = rationale
config["posture_timestamp"] = datetime.now().isoformat()

# 2. Adjust Parameters (Neutral adjustment)
# Risk-on: Thresholds down, RSI_oversold up, Confirmation down
# Risk-off: Thresholds up, RSI_oversold down, Confirmation up
# Neutral: Mixed / Moderate

v3 = config["v3_live"]

# Adjust thresholds based on specific stock performance
# 9988.HK strong -> lower threshold to capture trend
v3["prediction_thresholds"]["9988.HK"] = clamp(0.0065, 0.0010, 0.0100)
# 0700.HK/3690.HK weak/volatile -> higher threshold
v3["prediction_thresholds"]["0700.HK"] = clamp(0.0075, 0.0010, 0.0100)
v3["prediction_thresholds"]["3690.HK"] = clamp(0.0080, 0.0010, 0.0100)

# RSI / Confirmation
v3["rsi_oversold"] = clamp(38.0, 30, 45)
v3["rsi_overbought"] = clamp(70.0, 65, 80)
v3["swing_buy_confirmation_count"] = clamp(2, 1, 3)
v3["buy_cooldown_cycles"] = clamp(2, 1, 5)

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)

# Diff Calculation
diff = {
    "posture": f"{old_config.get('posture')} -> {new_posture}",
    "thresholds": {
        "0700.HK": f"{old_config['v3_live']['prediction_thresholds']['0700.HK']} -> {v3['prediction_thresholds']['0700.HK']}",
        "9988.HK": f"{old_config['v3_live']['prediction_thresholds']['9988.HK']} -> {v3['prediction_thresholds']['9988.HK']}",
        "3690.HK": f"{old_config['v3_live']['prediction_thresholds']['3690.HK']} -> {v3['prediction_thresholds']['3690.HK']}"
    },
    "rsi_oversold": f"{old_config['v3_live']['rsi_oversold']} -> {v3['rsi_oversold']}"
}

print(json.dumps(diff, indent=2, ensure_ascii=False))
