import json
from datetime import datetime, timezone

with open('config.json') as f:
    cfg = json.load(f)

cur = cfg['v3_live']
cur_pt = cur['prediction_thresholds']['0700.HK']
cur_os = cur['rsi_oversold']
cur_ob = cur['rsi_overbought']
cur_conf = cur['swing_buy_confirmation_count']
cur_cd = cur['buy_cooldown_cycles']

posture = 'neutral'
rationale = '2800.HK mom: +0.000531 (>0), 3033.HK mom: -0.000402 (<0) -> neutral'

def adjust(val, delta, lo, hi):
    new_val = val + delta
    if isinstance(val, float) and abs(val) < 1:
        new_val = round(new_val, 6)
    else:
        new_val = round(new_val, 1)
    return max(lo, min(hi, new_val))

delta_thresh = 0.0002
delta_os = 1.0
delta_ob = -1.0
delta_conf = 1
delta_cd = 1

new_pt = adjust(cur_pt, delta_thresh, 0.0010, 0.0100)
new_os = adjust(cur_os, delta_os, 35, 55)
new_ob = adjust(cur_ob, delta_ob, 65, 82)
new_conf = max(1, min(3, cur_conf + delta_conf))
new_cd = max(0, min(5, cur_cd + delta_cd))

cfg['posture'] = posture
cfg['posture_timestamp'] = datetime.now(timezone.utc).isoformat()
cfg['posture_rationale'] = rationale
cfg['v3_live']['prediction_thresholds'] = {'0700.HK': new_pt, '9988.HK': new_pt, '3690.HK': new_pt}
cfg['v3_live']['rsi_oversold'] = new_os
cfg['v3_live']['rsi_overbought'] = new_ob
cfg['v3_live']['swing_buy_confirmation_count'] = new_conf
cfg['v3_live']['buy_cooldown_cycles'] = new_cd

with open('config.json', 'w') as f:
    json.dump(cfg, f, indent=2)

print(f"posture: {posture}")
print(f"2800.HK momentum: +0.000531")
print(f"3033.HK momentum: -0.000402")
print()
print("Changed keys:")
print(f"  posture: risk_on -> {posture}")
print(f"  prediction_thresholds: {cur_pt} -> {new_pt}")
print(f"  rsi_oversold: {cur_os} -> {new_os}")
print(f"  rsi_overbought: {cur_ob} -> {new_ob}")
print(f"  swing_buy_confirmation_count: {cur_conf} -> {new_conf}")
print(f"  buy_cooldown_cycles: {cur_cd} -> {new_cd}")
