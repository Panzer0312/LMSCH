from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, cast
from sqlmodel import col, func, select

from spookhub import db
from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.models import EventLog

router = APIRouter(prefix="/api/events", tags=["events"])
HubDep = Annotated[Hub, Depends(get_hub)]
HOUR = 3600


@router.get("")
async def list_events(
    hub: HubDep,
    since: float = 0.0,
    kind: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[dict[str, Any]]:
    await hub.events.flush()
    q = select(EventLog).where(EventLog.ts >= since)
    if kind:
        q = q.where(EventLog.kind == kind)
    q = q.order_by(col(EventLog.ts).desc(), col(EventLog.id).desc()).limit(limit)

    def query() -> list[dict[str, Any]]:
        with db.session() as s:
            return [
                {
                    "id": e.id,
                    "ts": e.ts,
                    "level": e.level,
                    "kind": e.kind,
                    "message": e.message,
                    "data": e.data or {},
                }  # fmt: skip
                for e in s.exec(q)
            ]

    # SQLite I/O off the event loop: the scheduler fires cues from it.
    return await asyncio.to_thread(query)


@router.get("/stats")
async def stats(hub: HubDep, since: float | None = None) -> dict[str, Any]:
    await hub.events.flush()
    t0 = since if since is not None else time.time() - 24 * HOUR
    # Integer cast truncates; equal to floor for the positive epoch stamps stored here.
    hour = (cast(col(EventLog.ts) / HOUR, Integer) * HOUR).label("hour")
    q = (
        select(hour, func.count())
        .where(EventLog.kind == "trigger", EventLog.ts >= t0)
        .group_by(hour)
        .order_by(hour)
    )

    def query() -> list[tuple[float, int]]:
        with db.session() as s:
            return [(h, c) for h, c in s.exec(q)]

    rows = await asyncio.to_thread(query)
    return {
        "triggers": sum(c for _, c in rows),
        "by_hour": [{"hour_ts": int(h), "count": c} for h, c in rows],
    }
