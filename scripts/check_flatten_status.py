#!/usr/bin/env python3
from __future__ import annotations
import json, os
from futu import *
HOST=os.getenv('FUTU_OPEND_HOST','127.0.0.1')
PORT=int(os.getenv('FUTU_OPEND_PORT','11112'))
ctx=OpenSecTradeContext(filter_trdmarket=TrdMarket.NONE, host=HOST, port=PORT, security_firm=SecurityFirm.FUTUSECURITIES)
out={'orders':[],'positions':[],'errors':[]}
try:
    ret, accs=ctx.get_acc_list()
    if ret!=RET_OK:
        out['errors'].append({'get_acc_list':str(accs)})
    else:
        acc_ids=[]
        for _,r in accs.iterrows():
            if 'SIMULATE' in str(r.get('trd_env','')):
                acc_ids.append(int(r['acc_id']))
        for acc_id in acc_ids:
            ret,o=ctx.order_list_query(trd_env=TrdEnv.SIMULATE, acc_id=acc_id, refresh_cache=True)
            if ret==RET_OK and not o.empty:
                for _,r in o.iterrows():
                    out['orders'].append({k: (r.get(k).item() if hasattr(r.get(k),'item') else r.get(k)) for k in ['code','stock_name','trd_side','order_type','order_status','order_id','qty','price','dealt_qty','dealt_avg_price','last_err_msg','remark','create_time','updated_time'] if k in r})
                    out['orders'][-1]['acc_id']=acc_id
            elif ret!=RET_OK:
                out['errors'].append({'order_list':acc_id,'data':str(o)})
            ret,p=ctx.position_list_query(trd_env=TrdEnv.SIMULATE, acc_id=acc_id, refresh_cache=True)
            if ret==RET_OK and not p.empty:
                for _,r in p.iterrows():
                    try: qty=float(r.get('qty') or 0)
                    except: qty=0
                    try: mv=float(r.get('market_val') or 0)
                    except: mv=0
                    if qty or mv:
                        out['positions'].append({k: (r.get(k).item() if hasattr(r.get(k),'item') else r.get(k)) for k in ['code','stock_name','qty','can_sell_qty','nominal_price','market_val','unrealized_pl'] if k in r})
                        out['positions'][-1]['acc_id']=acc_id
            elif ret!=RET_OK:
                out['errors'].append({'position_list':acc_id,'data':str(p)})
finally:
    ctx.close()
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
