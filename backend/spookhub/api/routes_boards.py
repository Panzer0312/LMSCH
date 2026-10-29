from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from spookhub.api.deps import get_hub
from spookhub.boards.client import WledError
from spookhub.boards.recommend import run_checks
from spookhub.hub import Hub
from spookhub.models import LightState
from spookhub.tasks import spawn

router = APIRouter(prefix="/api/boards", tags=["boards"])
HubDep = Annotated[Hub, Depends(get_hub)]


class AddIn(BaseModel):
    ip: str


class NameIn(BaseModel):
    name: str


class BoardPatch(BaseModel):
    name: str | None = None
    offset_ms: float | None = Field(default=None, ge=-500, le=500)


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
def patch_board(mac: str, body: BoardPatch, hub: HubDep) -> dict[str, Any]:
    _board(hub, mac)
    if body.name is not None:
        hub.boards.rename(mac, body.name.strip())
    if body.offset_ms is not None:
        hub.boards.set_offset(mac, body.offset_ms)
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
    spawn(hub.boards.identify(mac))
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


@router.get("/{mac}/capture")
async def capture(mac: str, hub: HubDep) -> LightState:
    """Current live state of a board as a LightState (tweak in WLED UI, then grab it)."""
    b = _board(hub, mac)
    try:
        data, _ = await hub.wled.state_info(b.ip, 2.0)
    except WledError as e:
        raise HTTPException(502, f"board unreachable: {e}") from e
    st = data.get("state", {})
    segs = st.get("seg") or [{}]
    main = next((s for s in segs if s.get("id") == st.get("mainseg", 0)), segs[0])
    cols = [tuple(c[:3]) for c in main.get("col", []) if isinstance(c, list) and len(c) >= 3]
    return LightState(
        on=st.get("on"),
        bri=st.get("bri"),
        fx=main.get("fx"),
        pal=main.get("pal"),
        sx=main.get("sx"),
        ix=main.get("ix"),
        colors=cols[:3] or None,  # type: ignore[arg-type]
    )


@router.get("/{mac}/recommendations")
async def recommendations(mac: str, hub: HubDep) -> list[dict[str, Any]]:
    b = _board(hub, mac)
    try:
        cfg = await hub.wled.get_cfg(b.ip)
    except WledError as e:
        raise HTTPException(502, f"board unreachable: {e}") from e
    checks = run_checks(cfg)
    for c in checks:
        if c.id == "wifi_sleep":
            b.wifi_sleep, b.wifi_sleep_at = not c.ok, time.time()  # feeds preflight
    return [
        {"id": c.id, "title": c.title, "ok": c.ok, "detail": c.detail, "fixable": c.fix is not None}
        for c in checks
    ]


@router.post("/{mac}/recommendations/{check_id}/apply")
async def apply_recommendation(mac: str, check_id: str, hub: HubDep) -> dict[str, bool]:
    b = _board(hub, mac)
    try:
        cfg = await hub.wled.get_cfg(b.ip)
        check = next((c for c in run_checks(cfg) if c.id == check_id), None)
        if check is None:
            raise HTTPException(404, "unknown check")
        if check.fix is None:
            return {"ok": True}
        await hub.wled.set_cfg(b.ip, check.fix)
        if check.id == "wifi_sleep":
            b.wifi_sleep, b.wifi_sleep_at = False, time.time()
    except WledError as e:
        raise HTTPException(502, f"board unreachable: {e}") from e
    hub.bus.event("info", f"Applied '{check.title}' on {b.name or mac}", mac=mac)
    return {"ok": True}
