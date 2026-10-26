from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.models import LightState

router = APIRouter(prefix="/api/boards", tags=["boards"])
HubDep = Annotated[Hub, Depends(get_hub)]


class AddIn(BaseModel):
    ip: str


class NameIn(BaseModel):
    name: str


def _board(hub: Hub, mac: str) -> Any:
    b = hub.boards.boards.get(mac)
    if b is None:
        raise HTTPException(404, "unknown board")
    return b


@router.get("")
def list_boards(hub: HubDep) -> list[dict[str, Any]]:
    return hub.boards.snapshot()


@router.post("/scan")
def scan(hub: HubDep) -> dict[str, bool]:
    return {"started": hub.scan()}


@router.post("/add")
async def add(body: AddIn, hub: HubDep) -> dict[str, Any]:
    b = await hub.boards.probe(body.ip.strip(), "manual")
    if b is None:
        raise HTTPException(404, f"no WLED board answered at {body.ip}")
    return b.snapshot()


@router.post("/{mac}/adopt")
async def adopt(mac: str, body: NameIn, hub: HubDep) -> dict[str, Any]:
    _board(hub, mac)
    b = await hub.boards.adopt(mac, body.name.strip())
    hub.engine.boards_changed()
    return b.snapshot()


@router.patch("/{mac}")
def rename(mac: str, body: NameIn, hub: HubDep) -> dict[str, Any]:
    _board(hub, mac)
    hub.boards.rename(mac, body.name.strip())
    return hub.boards.boards[mac].snapshot()


@router.delete("/{mac}")
async def remove(mac: str, hub: HubDep) -> dict[str, bool]:
    _board(hub, mac)
    await hub.boards.remove(mac)
    hub.engine.boards_changed()
    return {"ok": True}


@router.post("/{mac}/identify")
def identify(mac: str, hub: HubDep) -> dict[str, bool]:
    _board(hub, mac)
    asyncio.create_task(hub.boards.identify(mac))
    return {"ok": True}


@router.post("/{mac}/state")
def manual_state(mac: str, body: LightState, hub: HubDep) -> dict[str, bool]:
    """Manual override (quick on/off, brightness). Cleared on next profile activation."""
    b = _board(hub, mac)
    if not b.adopted:
        raise HTTPException(409, "adopt the board first")
    hub.boards.set_manual(mac, body)
    return {"ok": True}


@router.post("/{mac}/refresh")
async def refresh(mac: str, hub: HubDep) -> dict[str, Any]:
    _board(hub, mac)
    await hub.boards.refresh_meta(mac)
    return hub.boards.boards[mac].snapshot()
