#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
from collections import deque
from pathlib import Path
from typing import AsyncIterator

import aiofiles
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

APP_HOST = os.getenv("KIRO_BRIDGE_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("KIRO_BRIDGE_PORT", "18888"))
LOG_PATH = Path(
    os.getenv(
        "V3_LOG_PATH",
        "/home/tsukii0607/.openclaw/workspace/skills/futu-api/v3_pipeline/logs/v3_live_trade.log",
    )
)

app = FastAPI(title="Kiro Bridge", version="1.0.0")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("index.html")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "log_exists": LOG_PATH.exists(),
            "log_path": str(LOG_PATH),
        }
    )


def tail_lines(path: Path, max_lines: int = 120) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=max_lines)]


async def follow_log(path: Path, poll_seconds: float = 0.4) -> AsyncIterator[str]:
    while not path.exists():
        await asyncio.sleep(poll_seconds)

    stream = await aiofiles.open(path, "r", encoding="utf-8", errors="replace")
    await stream.seek(0, os.SEEK_END)
    last_inode = path.stat().st_ino

    try:
        while True:
            line = await stream.readline()
            if line:
                yield line.rstrip("\n")
                continue

            yield ""
            await asyncio.sleep(poll_seconds)

            if path.exists() and path.stat().st_ino != last_inode:
                await stream.close()
                stream = await aiofiles.open(path, "r", encoding="utf-8", errors="replace")
                last_inode = path.stat().st_ino
    finally:
        await stream.close()


@app.websocket("/ws/v3_logs")
async def ws_v3_logs(ws: WebSocket) -> None:
    await ws.accept()
    for line in tail_lines(LOG_PATH):
        await ws.send_text(line)

    try:
        async for new_line in follow_log(LOG_PATH):
            if new_line:
                await ws.send_text(new_line)
            else:
                await ws.send_text("[HEARTBEAT] ws_alive")
    except WebSocketDisconnect:
        return


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("kiro_bridge:app", host=APP_HOST, port=APP_PORT, reload=False)
