from __future__ import annotations

import math
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.models import RuntimeSettings

router = APIRouter(prefix="/api", tags=["show"])
HubDep = Annotated[Hub, Depends(get_hub)]
PLAYER_KEYS = ("mpv_vo", "mpv_gpu_context", "mpv_drm_connector", "mpv_drm_mode", "mpv_audio_device")


class PlayIn(BaseModel):
    from_s: float = Field(default=0.0, ge=0)


class SeekIn(BaseModel):
    t: float = Field(ge=0)


class FlagIn(BaseModel):
    on: bool


class LockIn(BaseModel):
    on: bool
    pin: str | None = Field(default=None, max_length=64)


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


@router.post("/show_lock")
async def show_lock(body: LockIn, hub: HubDep) -> dict[str, bool]:
    """Turning the lock on is always allowed; turning it off needs the PIN when one is set."""
    if not body.on and hub.auth.pin_enabled and not hub.auth.check_pin(body.pin or ""):
        raise HTTPException(401, "wrong PIN")
    hub.settings.update(show_lock=body.on)  # publishes "settings"
    if body.on:
        await hub.engine.end_setup_for_lock()
    return {"show_lock": body.on}


@router.get("/settings")
def get_settings(hub: HubDep) -> RuntimeSettings:
    return hub.settings.value


@router.put("/settings")
async def put_settings(body: dict[str, Any], hub: HubDep) -> RuntimeSettings:
    body.pop("active_profile_id", None)  # owned by the engine
    body.pop("show_lock", None)  # owned by the lock endpoints
    s = hub.settings.value
    # These restart mpv: never under a running show or setup action.
    changed = [k for k in PLAYER_KEYS if k in body and body[k] != getattr(s, k)]
    if changed:
        why = hub.engine.player_blocker()
        if why:
            raise HTTPException(409, f"cannot change {', '.join(changed)}: {why}")

    # Caps may change in the same request, so clamp against the effective new cap.
    def num(key: str, default: float) -> float:
        v = body.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise HTTPException(422, f"{key} must be a finite number")
        try:
            f = float(v)  # huge ints raise OverflowError here
        except OverflowError as e:
            raise HTTPException(422, f"{key} must be a finite number") from e
        if not math.isfinite(f):
            raise HTTPException(422, f"{key} must be a finite number")
        return f

    vcap = num("volume_cap", s.volume_cap)
    bcap = num("brightness_cap", s.brightness_cap)
    # Also pulls an existing master down when only the cap is lowered.
    body["master_volume"] = min(num("master_volume", s.master_volume), vcap)
    body["master_brightness"] = min(num("master_brightness", s.master_brightness), bcap)
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
