#!/usr/bin/env python3
"""
Quant Engine v2 - Config Module
=====================================
✅ 支援 SIMULATE / LIVE 切換
✅ 美股專用參數
✅ 風控參數集中管理
"""

import os
import json
from dataclasses import dataclass, asdict


def _detect_opend_host() -> str:
    """
    Resolve OpenD host with safer fallbacks (especially on WSL2 where host IP may change).

    Priority:
    1) FUTU_OPEND_HOST / OPEND_HOST / FUTU_HOST env override
    2) nameserver in /etc/resolv.conf (commonly Windows host gateway in WSL2)
    3) conventional WSL gateway
    4) localhost
    """
    env_host = (
        os.environ.get("FUTU_OPEND_HOST")
        or os.environ.get("OPEND_HOST")
        or os.environ.get("FUTU_HOST")
    )
    if env_host:
        return env_host

    resolv = "/etc/resolv.conf"
    try:
        with open(resolv, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("nameserver "):
                    ip = s.split()[1].strip()
                    if ip:
                        return ip
    except Exception:
        pass

    return "192.168.128.1"

# ============================================================
# ⚠️  TRADE MODE - 唯一控制開關
# ============================================================
TRADE_MODE = "SIMULATE"   # 只改呢一行就可以切換 "SIMULATE" / "LIVE"

# ============================================================
# Futu OpenD 連線參數
# ============================================================
OPEND_HOST  = _detect_opend_host()
OPEND_PORT  = int(os.environ.get("FUTU_OPEND_PORT", "11111"))
# 安全起見：不提供預設交易密碼，必須由環境變數注入
TRADE_PWD   = os.environ.get("FUTU_TRADE_PWD", "")

# ============================================================
# 目標市場
# ============================================================
MARKET = "US"   # "US" / "HK"

# ============================================================
# 股票池
# ============================================================
STOCK_LIST = ["US.TSLA", "US.F", "US.NVDA"]

# ============================================================
# 風控參數（小資金實盤）
# ============================================================
@dataclass
class RiskConfig:
    # 倉位控制
    max_position_pct: float = 0.10       # 單股最大 10% 資金
    max_total_exposure: float = 0.60     # 總倉位最大 60%
    min_qty: int = 1                     # 最小下單量

    # 損益控制
    stop_loss_pct: float = 0.03          # 止損 3%
    stop_profit_pct: float = 0.08        # 止盈 8%
    max_daily_loss_pct: float = 0.03     # 單日最大虧損 3% → 停止交易

    # 風控熔斷
    max_consecutive_errors: int = 5      # 連續錯誤 5 次 → 熔斷

    # 交易頻率控制
    cooldown_seconds: int = 60           # 同一股票 60 秒內不重複下單

    # 模型信號門檻
    signal_confidence_threshold: float = 0.60   # 低於 60% confidence → HOLD

    # 日誌控制
    suppress_no_position_logs: bool = False  # 抑制 "NoPosition" 拒絕日誌（減少噪音）

RISK_CONFIG = RiskConfig()

# ============================================================
# 系統參數
# ============================================================
NET_ASSETS       = 999_998      # 模擬戶口實際資金（用於計算倉位）
RETRAIN_INTERVAL = 86400        # 每天重訓一次（秒）
POSITION_SYNC_INTERVAL = 300    # 每 5 分鐘強制同步持倉（秒）
ORDER_COOLDOWN_HOURS = 1        # 同股票買入冷卻時間（小時），避免 OrderPending 過長
LOOP_INTERVAL    = 300          # 主循環間隔（秒）
KLINE_COUNT      = 500          # 取 K 線數量

# ============================================================
# Telegram 通知配置（建議使用環境變數）
# ============================================================
TG_TOKEN    = os.environ.get("FUTU_TG_TOKEN", "")
TG_CHAT_ID  = os.environ.get("FUTU_TG_CHAT_ID", "")
TG_ENABLED  = os.environ.get("FUTU_TG_ENABLED", "1") == "1" and bool(TG_TOKEN and TG_CHAT_ID)

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
LOG_PATH    = os.path.join(BASE_DIR, "quant_v2.log")
STATE_PATH  = os.path.join(BASE_DIR, "state.json")

# ============================================================
# Helper
# ============================================================
def is_live() -> bool:
    return TRADE_MODE == "LIVE"

def print_config():
    print("=" * 60)
    print(f"  Quant Engine v2  |  {TRADE_MODE} MODE  |  {MARKET}")
    print("=" * 60)
    print(f"  股票池:           {STOCK_LIST}")
    print(f"  模擬資金:         ${NET_ASSETS:,}")
    print(f"  單股上限:         {RISK_CONFIG.max_position_pct*100:.0f}%")
    print(f"  最大回撤:         {RISK_CONFIG.max_daily_loss_pct*100:.0f}%")
    print(f"  止損:             {RISK_CONFIG.stop_loss_pct*100:.0f}%")
    print(f"  止盈:             {RISK_CONFIG.stop_profit_pct*100:.0f}%")
    print(f"  信號門檻:         {RISK_CONFIG.signal_confidence_threshold*100:.0f}%")
    print(f"  冷卻時間:         {RISK_CONFIG.cooldown_seconds}s")
    print("=" * 60)
