#!/usr/bin/env python3
"""測試 WSL2 連接 Windows FutuOpenD 的多種方案"""

import socket
import sys
import os
from pathlib import Path

def test_connection(host: str, port: int, timeout: int = 3) -> bool:
    """測試 TCP 連線"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception as e:
        return False

def load_env():
    """載入 .env"""
    env_file = Path(".env")
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    os.environ[k] = v.strip().strip('"').strip("'")

def main():
    print("=" * 60)
    print("🔍 WSL2 → Windows FutuOpenD 連接測試")
    print("=" * 60)
    
    # 方案 1: 使用 127.0.0.1 (WSL2 內部 localhost)
    print("\n📍 方案 1: 127.0.0.1:11111 (WSL2 內部)")
    if test_connection("127.0.0.1", 11111):
        print("   ✅ 成功！FutuOpenD 監聽 WSL2 localhost")
    else:
        print("   ❌ 失敗！FutuOpenD 不在 WSL2 內部")
    
    # 方案 2: 使用 Windows 主機 IP
    print("\n📍 方案 2: 192.168.128.1:11111 (Windows 主機)")
    if test_connection("192.168.128.1", 11111):
        print("   ✅ 成功！可透過主機 IP 連接")
        print("   💡 推薦：使用 FUTU_OPEND_HOST=192.168.128.1")
    else:
        print("   ❌ 失敗！需要 Windows 端口轉發")
    
    # 方案 3: 使用主機名 (windows-host)
    print("\n📍 方案 3: windows-host:11111 (主機名)")
    try:
        if test_connection("windows-host", 11111):
            print("   ✅ 成功！主機名解析正常")
    except:
        print("   ❌ 失敗！需要配置 /etc/hosts")
    
    # 方案 4: 使用宿主機名稱 (host.docker.internal 風格)
    print("\n📍 方案 4: host.wsl.internal:11111 (WSL 特殊主機名)")
    try:
        if test_connection("host.wsl.internal", 11111):
            print("   ✅ 成功！WSL 內部主機名可用")
    except:
        print("   ❌ 失敗！此主機名不存在")
    
    # 方案 5: 嘗試 Tailscale IP (如果有)
    print("\n📍 方案 5: Tailscale IP (如果啟用)")
    try:
        import subprocess
        result = subprocess.run(['ip', 'addr', 'show', 'tailscale0'], 
                              capture_output=True, text=True)
        if '100.' in result.stdout:
            print("   ℹ️  檢測到 Tailscale，可嘗試使用 Tailscale IP")
    except:
        pass
    
    # 總結
    print("\n" + "=" * 60)
    print("📋 總結與建議")
    print("=" * 60)
    
    # 檢查 .env 配置
    load_env()
    current_host = os.environ.get('FUTU_OPEND_HOST', '127.0.0.1')
    print(f"\n當前 .env 配置: FUTU_OPEND_HOST={current_host}")
    
    if test_connection("192.168.128.1", 11111):
        print("\n✅ 推薦方案: 已可連接，保持現有配置")
    elif test_connection("127.0.0.1", 11111):
        print("\n⚠️  需要修改: 請將 FUTU_OPEND_HOST 改為 127.0.0.1")
    else:
        print("\n🔧 需要 Windows 端配置:")
        print("   1. 在 Windows PowerShell (管理員) 執行:")
        print('      netsh interface portproxy add v4tov4 listenport=11111 listenaddress=0.0.0.0 connectport=11111 connectaddress=127.0.0.1')
        print("   2. 或修改 FutuOpenD 配置監聽 0.0.0.0")
        print("\n   然後在 WSL 使用: FUTU_OPEND_HOST=192.168.128.1")

if __name__ == "__main__":
    main()
