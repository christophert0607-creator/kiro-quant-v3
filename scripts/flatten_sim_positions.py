#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone

from futu import *

HOST = os.getenv('FUTU_OPEND_HOST', '127.0.0.1')
PORT = int(os.getenv('FUTU_OPEND_PORT', '11112'))
TRD_ENV = TrdEnv.SIMULATE
FIRM = SecurityFirm.FUTUSECURITIES


def fnum(x, default=0.0):
    try:
        if x is None:
            return default
        if isinstance(x, str) and x.upper() == 'N/A':
            return default
        v = float(x)
        if math.isnan(v):
            return default
        return v
    except Exception:
        return default


def market_from_code(code: str):
    code = str(code)
    if code.startswith('HK.'):
        return TrdMarket.HK
    if code.startswith('US.'):
        return TrdMarket.US
    return TrdMarket.NONE


def close_price(nominal: float, side) -> float:
    if nominal <= 0:
        nominal = 1.0
    # Aggressive limit: sell below market / buy above market to improve fill chance.
    if side == TrdSide.SELL:
        px = nominal * 0.98
    else:
        px = nominal * 1.02
    return round(px, 3 if nominal < 20 else 2)


def main():
    out = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'host': HOST,
        'port': PORT,
        'env': 'SIMULATE',
        'cancel_results': [],
        'positions_before': [],
        'orders_sent': [],
        'positions_after': [],
        'errors': [],
    }
    ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.NONE, host=HOST, port=PORT, security_firm=FIRM)
    try:
        ret, accs = ctx.get_acc_list()
        if ret != RET_OK:
            out['errors'].append({'step': 'get_acc_list', 'data': str(accs)})
            print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
            return 2
        sim_accs = accs[accs['trd_env'].astype(str).str.contains('SIMULATE', na=False)] if not accs.empty else accs
        # Prefer all simulate securities accounts with HK/US auth.
        acc_ids = []
        for _, r in sim_accs.iterrows():
            acc_id = int(r['acc_id'])
            auth = str(r.get('trdmarket_auth', ''))
            if ('HK' in auth or 'US' in auth or auth == '' or 'SIMULATE' in str(r.get('trd_env',''))):
                acc_ids.append(acc_id)
        acc_ids = list(dict.fromkeys(acc_ids))
        out['accounts'] = acc_ids

        for acc_id in acc_ids:
            # Cancel open orders first on HK/US.
            try:
                ret, data = ctx.cancel_all_order(trd_env=TRD_ENV, acc_id=acc_id, trdmarket=TrdMarket.NONE)
                out['cancel_results'].append({'acc_id': acc_id, 'ret': int(ret), 'data': str(data)})
            except Exception as e:
                out['cancel_results'].append({'acc_id': acc_id, 'error': str(e)})

            ret, pos = ctx.position_list_query(trd_env=TRD_ENV, acc_id=acc_id, refresh_cache=True)
            if ret != RET_OK:
                out['errors'].append({'step': 'position_before', 'acc_id': acc_id, 'data': str(pos)})
                continue
            if pos.empty:
                continue
            for _, r in pos.iterrows():
                code = str(r.get('code', ''))
                qty = fnum(r.get('qty'))
                can_sell = fnum(r.get('can_sell_qty'), qty)
                nominal = fnum(r.get('nominal_price')) or fnum(r.get('market_val')) / qty if qty else 0.0
                rec = {
                    'acc_id': acc_id,
                    'code': code,
                    'name': str(r.get('stock_name', '')),
                    'qty': qty,
                    'can_sell_qty': can_sell,
                    'nominal_price': nominal,
                    'market_val': fnum(r.get('market_val')),
                    'unrealized_pl': fnum(r.get('unrealized_pl')),
                }
                out['positions_before'].append(rec)
                if qty == 0:
                    continue
                if qty > 0:
                    side = TrdSide.SELL
                    order_qty = can_sell if can_sell > 0 else qty
                else:
                    side = TrdSide.BUY
                    order_qty = abs(qty)
                if order_qty <= 0:
                    out['orders_sent'].append({'acc_id': acc_id, 'code': code, 'status': 'skip_no_available_qty', 'qty': qty, 'can_sell_qty': can_sell})
                    continue
                price = close_price(nominal, side)
                try:
                    ret2, od = ctx.place_order(
                        price=price,
                        qty=order_qty,
                        code=code,
                        trd_side=side,
                        order_type=OrderType.NORMAL,
                        trd_env=TRD_ENV,
                        acc_id=acc_id,
                        remark='flatten_all_by_alice',
                    )
                    out['orders_sent'].append({
                        'acc_id': acc_id,
                        'code': code,
                        'side': str(side),
                        'qty': order_qty,
                        'price': price,
                        'ret': int(ret2),
                        'data': od.to_dict('records') if hasattr(od, 'to_dict') else str(od),
                    })
                    time.sleep(2.2)  # place_order limit: 15/30s
                except Exception as e:
                    out['orders_sent'].append({'acc_id': acc_id, 'code': code, 'side': str(side), 'qty': order_qty, 'price': price, 'error': str(e)})

        time.sleep(3)
        for acc_id in acc_ids:
            ret, pos = ctx.position_list_query(trd_env=TRD_ENV, acc_id=acc_id, refresh_cache=True)
            if ret == RET_OK and not pos.empty:
                for _, r in pos.iterrows():
                    qty = fnum(r.get('qty'))
                    if qty != 0:
                        out['positions_after'].append({
                            'acc_id': acc_id,
                            'code': str(r.get('code','')),
                            'name': str(r.get('stock_name','')),
                            'qty': qty,
                            'can_sell_qty': fnum(r.get('can_sell_qty'), qty),
                            'nominal_price': fnum(r.get('nominal_price')),
                            'market_val': fnum(r.get('market_val')),
                            'unrealized_pl': fnum(r.get('unrealized_pl')),
                        })
            elif ret != RET_OK:
                out['errors'].append({'step': 'position_after', 'acc_id': acc_id, 'data': str(pos)})
    finally:
        ctx.close()

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
