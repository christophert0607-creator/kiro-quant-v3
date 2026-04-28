#!/usr/bin/env python3
import os, yaml, traceback, sys

for k, v in {
    'FUTU_OPEND_HOST': '127.0.0.1', 'FUTU_OPEND_PORT': '11112',
    'FUTU_MARKET_PREFIX': 'HK', 'FUTU_TRD_ENV': 'SIMULATE',
    'FUTU_TARGET_ACC_ID': '18526451', 'AUTO_TRADING': 'true', 'PAPER_TRADING': 'true',
    'ENABLE_SCREENER': '1', 'SCREEN_TOP_N': '15', 'SCREEN_RSI_MIN': '15'
}.items():
    os.environ[k] = v

with open('v3_live.yaml') as f:
    cfg = yaml.safe_load(f)

for k, v in cfg.items():
    sys.stderr.write(f'{k!r}: {v!r} ({type(v).__name__})\n')
