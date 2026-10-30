from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from spookhub.api.deps import get_hub
from spookhub.hub import Hub

router = APIRouter(prefix="/api/scene", tags=["scene"])
HubDep = Annotated[Hub, Depends(get_hub)]


class PinIn(BaseModel):
    profile_id: int | None


@router.get("")
def state(hub: HubDep) -> dict[str, Any]:
    return hub.engine.scene_state()


@router.post("/skip")
async def skip(hub: HubDep) -> dict[str, Any]:
    try:
        return await hub.engine.scene_skip()
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/pin")
async def pin(body: PinIn, hub: HubDep) -> dict[str, Any]:
    try:
        return await hub.engine.scene_pin(body.profile_id)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
