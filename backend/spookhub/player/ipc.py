"""Minimal async client for mpv's JSON IPC protocol over a unix socket."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("spookhub.mpv.ipc")

# mpv lines are small for the properties we observe, but the default 64 KiB limit would kill the
# reader outright on an unexpectedly large reply (e.g. someone querying track-list).
_READ_LIMIT = 1 << 20


class MpvError(Exception):
    pass


class MpvIpc:
    def __init__(self) -> None:
        self.connected: bool = False
        self.closed: asyncio.Event = asyncio.Event()
        self.on_event: Callable[[dict[str, Any]], None] | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        # Start at 1: older mpv versions echo request_id 0 for requests that omitted it.
        self._next_request_id: int = 1
        self._next_observe_id: int = 1

    async def connect(self, path: Path, timeout: float = 5.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(str(path), limit=_READ_LIMIT)
                break
            except (FileNotFoundError, ConnectionRefusedError) as exc:
                # mpv only creates the socket some time after the process starts.
                if loop.time() >= deadline:
                    raise MpvError(f"could not connect to {path}: {exc}") from exc
                await asyncio.sleep(0.05)
        self._reader = reader
        self._writer = writer
        self.connected = True
        self.closed.clear()
        self._reader_task = asyncio.create_task(self._read_loop(), name="mpv-ipc-reader")

    async def command(self, *args: Any, timeout: float = 2.0) -> Any:
        writer = self._writer
        if not self.connected or writer is None:
            raise MpvError("not connected")
        request_id = self._next_request_id
        self._next_request_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        line = json.dumps({"command": list(args), "request_id": request_id}) + "\n"
        try:
            writer.write(line.encode())
            await writer.drain()
            async with asyncio.timeout(timeout):
                return await fut
        except TimeoutError as exc:
            raise MpvError(f"timeout waiting for {args[0] if args else '?'}") from exc
        except (ConnectionError, OSError) as exc:
            raise MpvError(f"write failed: {exc}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def set_property(self, name: str, value: Any) -> None:
        await self.command("set_property", name, value)

    async def get_property(self, name: str) -> Any:
        return await self.command("get_property", name)

    async def observe(self, name: str) -> int:
        observe_id = self._next_observe_id
        self._next_observe_id += 1
        await self.command("observe_property", observe_id, name)
        return observe_id

    async def close(self) -> None:
        task = self._reader_task
        self._reader_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        writer = self._writer
        self._writer = None
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        self._mark_closed()

    async def _read_loop(self) -> None:
        reader = self._reader
        assert reader is not None
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    log.debug("skipping unparsable mpv line: %r", raw[:200])
                    continue
                if isinstance(msg, dict):
                    self._dispatch(msg)
        except (ConnectionError, OSError, ValueError) as exc:
            # ValueError: readline raises it when a line exceeds the stream limit.
            log.warning("mpv ipc read failed: %s", exc)
        finally:
            self._mark_closed()

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "event" in msg:
            if self.on_event is not None:
                try:
                    self.on_event(msg)
                except Exception:
                    # A buggy listener must not take down the only reader of the socket.
                    log.exception("mpv on_event handler failed")
            return
        request_id = msg.get("request_id")
        if not isinstance(request_id, int):
            return
        fut = self._pending.get(request_id)
        if fut is None or fut.done():
            return
        error = msg.get("error")
        if error != "success":
            fut.set_exception(MpvError(str(error)))
        else:
            fut.set_result(msg.get("data"))

    def _mark_closed(self) -> None:
        self.connected = False
        pending = list(self._pending.values())
        self._pending.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(MpvError("disconnected"))
        self.closed.set()
