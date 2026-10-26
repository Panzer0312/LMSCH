"""Fire-and-forget tasks with a strong reference (the loop only keeps weak ones)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_done)
    return t


def _done(t: asyncio.Task[Any]) -> None:
    _tasks.discard(t)
    if not t.cancelled() and t.exception() is not None:
        log.error("background task failed", exc_info=t.exception())
