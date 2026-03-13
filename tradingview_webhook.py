#!/usr/bin/env python3
"""
TradingView Webhook 伺服器
接收 TradingView 的 Alert 信號，轉發到 Kiro Quant V3
"""

from flask import Flask, request, jsonify
import logging
import requests
import os
from datetime import datetime

app = Flask(__name__)

# 配置日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Kiro Quant API 配置
KIRO_API_URL = os.environ.get('KIRO_API_URL', 'http://localhost:18899')

# 簡單的認證
API_KEY = os.environ.get('TV_WEBHOOK_KEY', 'kiro_quant_2026')

@app.route('/webhook', methods=['POST'])
def webhook():
    """接收 TradingView Alert"""
    try:
        # 驗證 API Key
        auth_header = request.headers.get('X-API-Key')
        if auth_header != API_KEY:
            return jsonify({'error': 'Unauthorized'}), 401
        
        data = request.get_json()
        logger.info(f"收到 TradingView 信號: {data}")
        
        # 解析信號
        signal = data.get('signal', '').upper()
        symbol = data.get('symbol', '').upper()
        price = data.get('price', 0)
        
        # 記錄信號
        logger.info(f"📊 信號: {signal} | 標的: {symbol} | 價格: ${price}")
        
        # 轉發到 Kiro Quant (如果有 API)
        try:
            response = requests.post(
                f"{KIRO_API_URL}/signal",
                json={
                    'source': 'tradingview',
                    'signal': signal,
                    'symbol': symbol,
                    'price': price,
                    'timestamp': datetime.now().isoformat()
                },
                timeout=5
            )
            logger.info(f"Kiro API 回應: {response.status_code}")
        except Exception as e:
            logger.warning(f"轉發到 Kiro API 失敗: {e}")
        
        return jsonify({
            'status': 'success',
            'signal': signal,
            'symbol': symbol
        })
        
    except Exception as e:
        logger.error(f"Webhook 錯誤: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    """健康檢查"""
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()})

if __name__ == '__main__':
    port = int(os.environ.get('TV_WEBHOOK_PORT', 8777))
    logger.info(f"🚀 TradingView Webhook 服務啟動: http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
