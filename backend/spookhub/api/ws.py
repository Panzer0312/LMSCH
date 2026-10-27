from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from spookhub.auth import COOKIE
from spookhub.hub import Hub

router = APIRouter()


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    hub: Hub = websocket.app.state.hub
    if not hub.auth.valid_session(websocket.cookies.get(COOKIE)):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    q = hub.bus.subscribe()
    try:
        # Snapshot first so a (re)connecting phone is immediately consistent.
        await websocket.send_json(
            {
                "type": "snapshot",
                "data": {
                    "boards": hub.boards.snapshot(),
                    "show": hub.engine.snapshot(),
                    "settings": hub.settings.value.model_dump(),
                    "events": list(hub.bus.log),
                },
            }
        )

        async def drain_incoming() -> None:
            while True:
                await websocket.receive_text()  # pings only; UI never sends commands here

        reader = asyncio.create_task(drain_incoming())
        try:
            while True:
                getter = asyncio.create_task(q.get())
                done, _ = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
                if reader in done:
                    getter.cancel()
                    break
                await websocket.send_json(getter.result())
        finally:
            reader.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.bus.unsubscribe(q)
        with contextlib.suppress(Exception):
            await websocket.close()
