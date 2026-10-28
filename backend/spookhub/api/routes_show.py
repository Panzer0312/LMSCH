from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.models import RuntimeSettings

router = APIRouter(prefix="/api", tags=["show"])
HubDep = Annotated[Hub, Depends(get_hub)]


class PlayIn(BaseModel):
    from_s: float = Field(default=0.0, ge=0)


class SeekIn(BaseModel):
    t: float = Field(ge=0)


class FlagIn(BaseModel):
    on: bool


@router.get("/state")
def state(hub: HubDep) -> dict[str, Any]:
    return {
        "boards": hub.boards.snapshot(),
        "show": hub.engine.snapshot(),
        "settings": hub.settings.value.model_dump(),
        "events": list(hub.bus.log),
        "version": hub.version,
    }


@router.post("/trigger")
async def trigger(
    hub: HubDep,
    token: str | None = None,
    x_trigger_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """External trigger (PIR sensor etc.). Optional token via ?token= or X-Trigger-Token."""
    if not hub.auth.valid_trigger(x_trigger_token or token):
        raise HTTPException(401, "invalid trigger token")
    ok, msg = await hub.engine.trigger("external")
    return {"ok": ok, "message": msg}


@router.post("/show/trigger")
async def ui_trigger(hub: HubDep) -> dict[str, Any]:
    """Dashboard TRIGGER button (covered by the UI PIN, not the external token)."""
    ok, msg = await hub.engine.trigger("button")
    return {"ok": ok, "message": msg}


@router.post("/show/play")
async def play(body: PlayIn, hub: HubDep) -> dict[str, bool]:
    try:
        await hub.engine.play(body.from_s)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/show/stop")
async def stop(hub: HubDep) -> dict[str, bool]:
    await hub.engine.stop_show()
    return {"ok": True}


@router.post("/show/seek")
async def seek(body: SeekIn, hub: HubDep) -> dict[str, bool]:
    await hub.engine.seek(body.t)
    return {"ok": True}


@router.post("/show/pause")
async def pause(body: FlagIn, hub: HubDep) -> dict[str, bool]:
    await hub.engine.pause(body.on)
    return {"ok": True}


@router.post("/show/next")
async def next_profile(hub: HubDep) -> dict[str, bool]:
    await hub.engine.next_profile()
    return {"ok": True}


@router.post("/show/deactivate")
async def deactivate(hub: HubDep) -> dict[str, bool]:
    await hub.engine.deactivate()
    return {"ok": True}


@router.post("/blackout")
async def blackout(body: FlagIn, hub: HubDep) -> dict[str, bool]:
    await hub.engine.set_blackout(body.on)
    return {"ok": True}


@router.get("/settings")
def get_settings(hub: HubDep) -> RuntimeSettings:
    return hub.settings.value


@router.put("/settings")
async def put_settings(body: dict[str, Any], hub: HubDep) -> RuntimeSettings:
    body.pop("active_profile_id", None)  # owned by the engine
    body.pop("show_lock", None)  # owned by the lock endpoints
    s = hub.settings.value
    # Caps may change in the same request, so clamp against the effective new cap.
    try:
        vcap = float(body.get("volume_cap", s.volume_cap))
        bcap = float(body.get("brightness_cap", s.brightness_cap))
        # Also pulls an existing master down when only the cap is lowered.
        body["master_volume"] = min(float(body.get("master_volume", s.master_volume)), vcap)
        body["master_brightness"] = min(
            float(body.get("master_brightness", s.master_brightness)), bcap
        )
    except (TypeError, ValueError) as e:
        raise HTTPException(422, "numeric settings expected") from e
    try:
        return hub.settings.update(**body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


class AuditionIn(BaseModel):
    profile_id: int
    t: float = Field(ge=0)
    playing: bool


@router.post("/audition")
async def audition(body: AuditionIn, hub: HubDep) -> dict[str, Any]:
    """Browser preview reports its playhead (on play/pause/seek and ~1 s heartbeat)."""
    ok, msg = await hub.engine.audition_update(body.profile_id, body.t, body.playing)
    if not ok:
        raise HTTPException(409, msg)
    return {"ok": True, "message": msg}


@router.post("/audition/stop")
async def audition_stop(hub: HubDep) -> dict[str, bool]:
    await hub.engine.audition_stop()
    return {"ok": True}


@router.post("/profiles/{pid}/play_from")
async def play_from(pid: int, body: PlayIn, hub: HubDep) -> dict[str, bool]:
    try:
        await hub.engine.play_profile_from(pid, body.from_s)
    except KeyError as e:
        raise HTTPException(404, "profile not found") from e
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}
