import os
import sys
from pathlib import Path

# Ensure repo root on sys.path even when running from scripts/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v3_pipeline.core.futu_connector import FutuConnector

print('ENV FUTU_OPEND_HOST=', os.getenv('FUTU_OPEND_HOST'))
print('ENV FUTU_OPEND_PORT=', os.getenv('FUTU_OPEND_PORT'))
print('ENV FUTU_TRD_ENV=', os.getenv('FUTU_TRD_ENV'))
print('ENV FUTU_MARKET_PREFIX=', os.getenv('FUTU_MARKET_PREFIX'))
print('ENV FUTU_TARGET_ACC_ID=', os.getenv('FUTU_TARGET_ACC_ID'))

c = FutuConnector()
c.connect()

acc = c.discover_accounts()
print('accounts rows:', len(acc))
if len(acc):
    print(acc[['acc_id','trd_env','acc_type','acc_status']].head(10).to_string(index=False))

assets = c.get_sync_assets()
print('assets:', assets)

c.close()
print('✅ futu check OK')
