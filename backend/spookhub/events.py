"""In-process pub/sub bus. The WebSocket layer subscribes; everything else publishes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any

from sqlmodel import col, delete

from spookhub import db
from spookhub.models import EventLog

log = logging.getLogger(__name__)

LOG_SIZE = 50
RETENTION_S = 7 * 24 * 3600
PENDING_MAX = 5000  # unflushed run-log rows kept if the DB writer stalls


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self.log: deque[dict[str, Any]] = deque(maxlen=LOG_SIZE)
        self.pending: deque[dict[str, Any]] = deque(maxlen=PENDING_MAX)

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

    def event(self, level: str, message: str, kind: str = "info", **extra: Any) -> None:
        """Human-readable show event (dashboard log); also queued for the persistent run log."""
        entry = {"ts": time.time(), "level": level, "kind": kind, "message": message, **extra}
        self.log.append(entry)
        self.pending.append(entry)
        self.publish("event", entry)


class EventWriter:
    """Flushes buffered events to the DB off the event loop; prunes old rows."""

    def __init__(
        self,
        bus: EventBus,
        flush_s: float = 1.0,
        prune_s: float = 6 * 3600,
        retention_s: float = RETENTION_S,
    ) -> None:
        self.bus = bus
        self.flush_s, self.prune_s, self.retention_s = flush_s, prune_s, retention_s
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await self.prune()
        self._task = asyncio.create_task(self._loop(), name="event-writer")

    async def stop(self) -> None:
        t, self._task = self._task, None
        if t:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        async with self._lock:  # an in-flight thread write finishes first
            pass
        await self.flush()

    async def _loop(self) -> None:
        last_prune = time.monotonic()
        while True:
            await asyncio.sleep(self.flush_s)
            try:
                await self.flush()
                if time.monotonic() - last_prune >= self.prune_s:
                    last_prune = time.monotonic()
                    await self.prune()
            except Exception:  # noqa: BLE001
                log.exception("event log write failed")

    async def flush(self) -> None:
        async with self._lock:
            rows = []
            while self.bus.pending:
                rows.append(self.bus.pending.popleft())
            if not rows:
                return
            try:
                # Shielded: cancellation must not lose rows whose thread write is running.
                await asyncio.shield(asyncio.to_thread(_write, rows))
            except asyncio.CancelledError:
                raise
            except Exception:
                self._requeue(rows)
                raise

    def _requeue(self, rows: list[dict[str, Any]]) -> None:
        """Put failed rows back in front; if over the cap, drop the oldest."""
        merged = rows + list(self.bus.pending)
        self.bus.pending.clear()
        self.bus.pending.extend(merged[-PENDING_MAX:])

    async def prune(self) -> None:
        cutoff = time.time() - self.retention_s
        await asyncio.to_thread(_prune, cutoff)


def _clean(extra: dict[str, Any]) -> dict[str, Any] | None:
    """JSON-safe copy of the free-form event fields."""
    if not extra:
        return None
    return json.loads(json.dumps(extra, default=str))


def _write(rows: list[dict[str, Any]]) -> None:
    reserved = {"ts", "level", "kind", "message"}
    with db.session() as s:
        for e in rows:
            try:
                extra = _clean({k: v for k, v in e.items() if k not in reserved})
                s.add(
                    EventLog(
                        ts=e["ts"],
                        level=e["level"],
                        kind=e["kind"],
                        message=e["message"],
                        data=extra,
                    )
                )
            except (TypeError, ValueError, KeyError):
                log.warning("dropping unwritable event: %r", e.get("message"))
        s.commit()


def _prune(cutoff: float) -> None:
    with db.session() as s:
        s.exec(delete(EventLog).where(col(EventLog.ts) < cutoff))  # type: ignore[call-overload]
        s.commit()
