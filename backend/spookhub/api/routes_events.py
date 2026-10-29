from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import col, select

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


@router.get("/stats")
async def stats(hub: HubDep, since: float | None = None) -> dict[str, Any]:
    await hub.events.flush()
    t0 = since if since is not None else time.time() - 24 * HOUR
    with db.session() as s:
        stamps = s.exec(
            select(EventLog.ts).where(EventLog.kind == "trigger", EventLog.ts >= t0)
        ).all()
    buckets: dict[int, int] = {}
    for ts in stamps:
        h = int(ts // HOUR) * HOUR
        buckets[h] = buckets.get(h, 0) + 1
    return {
        "triggers": len(stamps),
        "by_hour": [{"hour_ts": h, "count": c} for h, c in sorted(buckets.items())],
    }
