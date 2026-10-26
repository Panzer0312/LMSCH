"""In-process pub/sub bus. The WebSocket layer subscribes; everything else publishes."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

LOG_SIZE = 50


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self.log: deque[dict[str, Any]] = deque(maxlen=LOG_SIZE)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs.discard(q)

    def publish(self, type_: str, data: Any = None) -> None:
        msg = {"type": type_, "data": data, "ts": time.time()}
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # A stalled browser tab must never block the show.
                pass

    def event(self, level: str, message: str, **extra: Any) -> None:
        """Human-readable show event (dashboard log)."""
        entry = {"ts": time.time(), "level": level, "message": message, **extra}
        self.log.append(entry)
        self.publish("event", entry)
