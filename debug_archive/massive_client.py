#!/usr/bin/env python3
"""
Massive.com API Client
Used as a free data source backup for US stocks
"""

import requests
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('MassiveClient')

class MassiveClient:
    """Client for fetching free stock data from Massive.com"""
    
    BASE_URL = "https://api.massive.com"
    API_KEY = "hHfrJP26kgrRpr_9rS3ykzexUCppb0wQ"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.API_KEY}",
            "Accept": "application/json"
        })
        
    def _request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make a request to the Massive API"""
        url = f"{self.BASE_URL}{endpoint}"
        try:
            logger.debug(f"Requesting {url} with params {params}")
            response = self.session.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 403:
                logger.warning(f"Access denied for {endpoint}: {response.text}")
                return None
            else:
                logger.error(f"Error {response.status_code} for {url}: {response.text}")
                return None
        except Exception as e:
            logger.error(f"Exception requesting {url}: {e}")
            return None

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """
        Get the latest available quote/price for a US stock.
        Uses the 'previous close' endpoint as it is reliable on the free plan.
        """
        # Endpoint: /v2/aggs/ticker/{stocksTicker}/prev
        endpoint = f"/v2/aggs/ticker/{symbol.upper()}/prev"
        data = self._request(endpoint)
        
        if data and data.get('results') and len(data['results']) > 0:
            result = data['results'][0]
            # Convert Massive format to our standard format
            return {
                'symbol': symbol.upper(),
                'market': 'US',
                'price': result.get('c'),      # Close price
                'open': result.get('o'),       # Open
                'high': result.get('h'),       # High
                'low': result.get('l'),        # Low
                'volume': result.get('v'),     # Volume
                'vwap': result.get('vw'),      # Volume Weighted Avg Price
                'timestamp': result.get('t'),  # Timestamp (ms)
                'time_str': datetime.fromtimestamp(result.get('t')/1000).isoformat(),
                'source': 'massive',
                'type': 'prev_close'           # Note that this is NOT real-time
            }
        return None

    def get_kline(self, symbol: str, timespan: str = "day", multiplier: int = 1, 
                  from_date: str = None, to_date: str = None, limit: int = 100) -> Optional[List[Dict]]:
        """
        Get historical aggregate bars (K-line).
        
        Args:
            symbol (str): Stock ticker (e.g., 'TSLA')
            timespan (str): 'minute', 'hour', 'day', 'week', 'month', 'quarter', 'year'
            multiplier (int): Size of the timespan multiplier (e.g., 5 for 5-minute bars)
            from_date (str): YYYY-MM-DD
            to_date (str): YYYY-MM-DD
            limit (int): Max results
        """
        if not from_date:
            from_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        if not to_date:
            to_date = datetime.now().strftime('%Y-%m-%d')
            
        # Endpoint: /v2/aggs/ticker/{stocksTicker}/range/{multiplier}/{timespan}/{from}/{to}
        endpoint = f"/v2/aggs/ticker/{symbol.upper()}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
        
        params = {
            "adjusted": "true",
            "sort": "desc",  # Latest first
            "limit": limit
        }
        
        data = self._request(endpoint, params)
        
        if data and data.get('results'):
            # Convert to standard list of dicts
            klines = []
            for item in data['results']:
                klines.append({
                    'time_key': datetime.fromtimestamp(item.get('t')/1000).strftime('%Y-%m-%d %H:%M:%S'),
                    'open': item.get('o'),
                    'close': item.get('c'),
                    'high': item.get('h'),
                    'low': item.get('l'),
                    'volume': item.get('v'),
                    'timestamp': item.get('t')
                })
            return klines
        return None

# Test execution
if __name__ == "__main__":
    client = MassiveClient()
    print("Running Massive Client Test...")
    
    # Test Quote
    quote = client.get_quote("TSLA")
    print(f"\nQuote for TSLA: {json.dumps(quote, indent=2)}")
    
    # Test K-line (Daily)
    print("\nFetching daily K-line for TSLA...")
    klines = client.get_kline("TSLA", timespan="day", limit=5)
    if klines:
        print(f"Got {len(klines)} bars. Latest: {klines[0]}")
    else:
        print("Failed to get K-lines.")
