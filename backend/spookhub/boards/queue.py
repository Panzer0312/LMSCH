"""Per-board 'latest wins' command mailbox.

A board has at most one pending command. Submitting replaces it, so a slow or offline board
never accumulates a backlog and always converges to the newest desired state.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Command:
    payload: dict[str, Any]
    critical: bool  # time-critical cue: short timeout, single quick retry
    seq: int = 0


SendFn = Callable[[Command, float], Awaitable[float]]


@dataclass
class LatestWinsQueue:
    send: SendFn
    cue_timeout_s: float = 0.3
    command_timeout_s: float = 2.0
    backoff_max_s: float = 15.0
    on_result: Callable[[Command, bool, float | None], None] | None = None
    dropped: int = 0
    _pending: Command | None = None
    _inflight: bool = False
    _seq: int = 0
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def submit(self, payload: dict[str, Any], critical: bool) -> None:
        self._seq += 1
        if self._pending is not None:
            self.dropped += 1
        self._pending = Command(payload, critical, self._seq)
        self._wake.set()

    @property
    def idle(self) -> bool:
        return self._pending is None and not self._inflight

    def _superseded(self, cmd: Command) -> bool:
        return self._pending is not None

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            cmd, self._pending = self._pending, None
            if cmd is None:
                continue
            self._inflight = True
            try:
                await self._deliver(cmd)
            finally:
                self._inflight = False
            if self._pending is not None:
                self._wake.set()

    async def _deliver(self, cmd: Command) -> bool:
        attempts = 2 if cmd.critical else 6
        timeout = self.cue_timeout_s if cmd.critical else self.command_timeout_s
        delay = 0.5
        for attempt in range(attempts):
            if attempt and self._superseded(cmd):
                return False
            try:
                ms = await self.send(cmd, timeout)
            except Exception as e:  # noqa: BLE001
                log.debug("send failed (attempt %d): %s", attempt + 1, e)
                if self.on_result:
                    self.on_result(cmd, False, None)
                if attempt + 1 < attempts and not cmd.critical:
                    # Wait for backoff or a newer command, whichever comes first.
                    try:
                        await asyncio.wait_for(self._wait_newer(cmd), timeout=delay)
                        return False
                    except TimeoutError:
                        pass
                    delay = min(delay * 2, self.backoff_max_s)
                continue
            if self.on_result:
                self.on_result(cmd, True, ms)
            return True
        # Gave up; reconciliation re-applies desired state when the board is back.
        return False

    async def _wait_newer(self, cmd: Command) -> None:
        while not self._superseded(cmd):
            await self._wake.wait()
            # Leave the flag set so _run picks the newer command up.
            if not self._superseded(cmd):
                self._wake.clear()
