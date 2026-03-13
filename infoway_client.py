import asyncio
import websockets
import json
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

class InfowayClient:
    def __init__(self, api_key, symbols):
        self.api_key = api_key
        self.symbols = symbols
        self.ws_url = f"wss://data.infoway.io/ws?business=stock&apikey={api_key}"
        self.cache = {}
        self.is_running = False
        self._thread = None
        self._loop = None

    async def _listen(self):
        while self.is_running:
            try:
                async with websockets.connect(self.ws_url) as websocket:
                    logger.info("Connected to Infoway WebSocket")
                    
                    # Subscribe to latest trades (Code 10000)
                    for symbol in self.symbols:
                        sub_data = {
                            "code": 10000,
                            "trace": f"kiro-{datetime.now().timestamp()}",
                            "data": {"codes": symbol}
                        }
                        await websocket.send(json.dumps(sub_data))
                        await asyncio.sleep(0.5)

                    # Heartbeat task
                    async def heartbeat():
                        while True:
                            ping = {"code": 10010, "trace": "ping"}
                            await websocket.send(json.dumps(ping))
                            await asyncio.sleep(30)

                    asyncio.create_task(heartbeat())

                    async for message in websocket:
                        data = json.loads(message)
                        if "data" in data and "price" in data["data"]:
                            sym = data["data"].get("codes", "unknown")
                            self.cache[sym] = {
                                "price": float(data["data"]["price"]),
                                "timestamp": datetime.now(),
                                "source": "INFOWAY"
                            }
            except Exception as e:
                logger.error(f"Infoway WS Error: {e}")
                await asyncio.sleep(5) # Reconnect delay

    def _start_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._listen())

    def start(self):
        self.is_running = True
        self._thread = threading.Thread(target=self._start_loop, daemon=True)
        self._thread.start()

    def get_price(self, symbol):
        data = self.cache.get(symbol)
        if data:
            # Check if stale (e.g., > 10 seconds)
            if (datetime.now() - data["timestamp"]).total_seconds() < 10:
                return data
        return None

    def stop(self):
        self.is_running = False
        if self._loop:
            self._loop.stop()
