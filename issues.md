# Futu API Issue Log

## 🔴 Issues to Resolve (2026-03-09)
1. **Real-Money Order Failed**
   - **Symbol**: US.F (1 share)
   - **Error**: "当前账户已停用，不支持下单"
   - **Context**: Real account cash balance is 0. Account might be inactive or restricted for OpenAPI trading.
   - **Action**: Check account activation status and fund USD into the real account.

## ✅ Completed Tasks
- Cleaned up duplicate FutuOpenD processes and successfully booted v10.0.
- Verified simulate account assets ($998,915.79 USD with 545 NVDA shares).
