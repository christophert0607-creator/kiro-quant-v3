# Kiro Standard: Futu OpenAPI Operations Manual

## 1) Connectivity Standard (WSL2)
- **API host**: always use `FUTU_OPEND_HOST` for client connectivity.
- **Recommended default**: `127.0.0.1` when OpenD runs in same environment.
- **API port**: `11111` (OpenAPI socket).

Environment examples:

```bash
export FUTU_OPEND_HOST=127.0.0.1
export FUTU_OPEND_PORT=11111
export FUTU_TRD_ENV=SIMULATE   # or REAL
export FUTU_TARGET_ACC_ID=123456789
export FUTU_TRADE_PASSWORD=******   # required when FUTU_TRD_ENV=REAL
```

## 2) Identity Mapping: Bull-ID vs Trade Account IDs
- **Bull-ID / login_account**: identity used to log in to Futu ecosystem.
- **Trade account ID (`acc_id`)**: concrete account used for placing orders and querying assets/positions.
- One Bull-ID can map to **multiple `acc_id`** values (cash/margin, different market scopes, disabled accounts).

Kiro policy:
1. Run account discovery on startup.
2. Log all discovered account metadata.
3. Explicitly bind runtime operations to `target_acc_id`.

Priority for target account selection:
1. `FUTU_TARGET_ACC_ID` environment variable.
2. `config.json` -> `futu.target_acc_id`.
3. First discovered account (fallback).

## 3) Account Discovery Protocol
The connector performs account discovery and logs:
- `acc_id`
- account type (`acc_type` / `sim_acc_type`)
- trade environment (`trd_env`)
- status (`ACTIVE` / `DISABLED` based on `is_trd_enabled`)

Use this discovery before any order routing and whenever account mappings change.

## 4) FutuOpenD XML/Port Hardening
To avoid dashboard conflicts:
- **11111**: OpenAPI socket (Python SDK)
- **18888**: Kiro FastAPI bridge/dashboard (reserved)
- **18889**: Futu OpenD web interface (**mandatory standard**)

In `FutuOpenD.xml`, keep the web UI bound to `18889`.

## 5) Login Protocol (SMS Verification)
When OpenD prompts phone verification:
1. Open Futu client/OpenD GUI on the host desktop session.
2. Complete SMS verification manually.
3. Confirm OpenD status is healthy and account permissions are visible.
4. Restart Kiro trading service after verification success.

If verification repeatedly appears:
- check device trust list,
- avoid rapid login/logout loops,
- ensure single active OpenD instance.

## 6) Recovery / Clean Boot Procedure
1. Stop Kiro services that use Futu contexts.
2. Kill stale OpenD or SDK clients:
   - Linux: `pkill -f OpenD` and `pkill -f futu` (adjust carefully)
3. Verify port state:
   - `ss -lntp | rg '11111|18888|18889'`
4. Start OpenD first.
5. Confirm web interface on `18889`.
6. Start Kiro services.
7. Validate connector heartbeat and account discovery logs.

## 7) Heartbeat & Runtime Reliability
Kiro connector heartbeat uses account info query to validate:
- context alive,
- trade environment authorized,
- account routing still valid.

On heartbeat/sync failure:
- keep last known account/position state,
- log warning to `stderr`,
- continue loop without crashing,
- retry next cycle.

## 8) Troubleshooting Checklist
- **Cannot trade, can quote**: likely trade context auth/account mapping issue. For REAL trading, confirm `FUTU_TRADE_PASSWORD` is set and trade unlock succeeds.
- **Wrong account values**: verify `FUTU_TARGET_ACC_ID` and account discovery logs.
- **Port conflict on web UI**: ensure OpenD web port is `18889`; free `18888` for Kiro bridge.
- **Frequent disconnects in WSL2**: verify `FUTU_OPEND_HOST`, firewall, and OpenD process health.

## 9) WSL2 Deployment Notes (Windows host + Linux runtime)
When FutuOpenD runs on Windows and strategy runs in WSL2:
- WSL2 `127.0.0.1` is the Linux VM loopback, **not** Windows loopback.
- If FutuOpenD only listens on `127.0.0.1`, WSL2 cannot connect directly.

Recommended options:
1. **Port forwarding on Windows (recommended)**
   - Run in elevated PowerShell:
   - `netsh interface portproxy add v4tov4 listenport=11111 listenaddress=0.0.0.0 connectport=11111 connectaddress=127.0.0.1`
2. **Change FutuOpenD bind address**
   - Configure FutuOpenD to listen on `0.0.0.0` instead of `127.0.0.1`.

Deployment checklist:
- Confirm FutuOpenD is running on Windows host and account is logged in.
- For WSL2, configure Windows `portproxy` (or OpenD `0.0.0.0`).
- Set `.env` / runtime env `FUTU_OPEND_HOST=<Windows host IP>` (for example `192.168.x.x`).

---
This document is the canonical operational baseline for Kiro Quant V3 Futu integration.
