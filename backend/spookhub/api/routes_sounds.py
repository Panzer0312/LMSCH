"""Soundboard: one-shot sounds on the fx player (manual scares). Allowed under the show lock."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from spookhub import db
from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.models import Media
from spookhub.player.ipc import MpvError

router = APIRouter(prefix="/api/sounds", tags=["sounds"])
HubDep = Annotated[Hub, Depends(get_hub)]


class SoundPlayIn(BaseModel):
    media_id: int
    volume: float = Field(default=1.0, ge=0, le=1.3, allow_inf_nan=False)


@router.post("/play")
async def play(body: SoundPlayIn, hub: HubDep) -> dict[str, bool]:
    if hub.engine.blackout:
        raise HTTPException(409, "blackout active")
    with db.session() as s:
        m = s.get(Media, body.media_id)
    if m is None or not Path(m.path).exists():
        raise HTTPException(404, "media not found")
    try:
        await hub.fx.play(m.path, volume=body.volume, owner="soundboard")
    except MpvError as e:
        raise HTTPException(503, f"sound player not ready: {e}") from e
    hub.bus.event("info", f"Sound: {m.filename}", kind="sound")
    return {"ok": True}


@router.post("/stop")
async def stop(hub: HubDep) -> dict[str, bool]:
    await hub.fx.stop()
    return {"ok": True}
