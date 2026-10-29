"""Setup helpers: output discovery, speaker test, trigger visibility."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.player.devices import drm_outputs
from spookhub.show.calibration import ensure_calibration_clip
from spookhub.show.engine import SetupBusy, SetupUnavailable
from spookhub.show.sounds import ensure_test_sound
from spookhub.show.testpattern import ensure_pattern

router = APIRouter(prefix="/api/setup", tags=["setup"])
preflight_router = APIRouter(prefix="/api/preflight", tags=["preflight"])
HubDep = Annotated[Hub, Depends(get_hub)]


@router.get("/outputs")
def outputs() -> list[dict[str, Any]]:
    return drm_outputs()


@router.get("/audio_devices")
async def audio_devices(hub: HubDep) -> list[dict[str, str]]:
    return await hub.engine.player.audio_devices()


@router.post("/test_sound")
async def test_sound(hub: HubDep) -> dict[str, bool]:
    """Returns once the sound has started; the engine restores idle when it ends."""
    eng = hub.engine
    why = eng.setup_blocker()
    if why:
        raise HTTPException(409, why)
    try:
        path = await ensure_test_sound(hub.cfg.paths.data_dir)
    except (RuntimeError, OSError) as e:
        raise HTTPException(500, f"could not render test sound: {e}") from e
    try:
        await eng.play_test_sound(str(path))
    except SetupBusy as e:
        raise HTTPException(409, str(e)) from e
    except SetupUnavailable as e:
        raise HTTPException(503, str(e)) from e
    return {"ok": True, "started": True}


class CalibrationIn(BaseModel):
    on: bool


@router.post("/calibration")
async def calibration(body: CalibrationIn, hub: HubDep) -> dict[str, bool]:
    """Starts or stops the sync calibration clip; refused while a show plays."""
    eng = hub.engine
    if not body.on:
        await eng.set_calibration(False)
        return {"ok": True, "calibrating": False}
    if not eng.calibrating:
        why = eng.setup_blocker()
        if why:
            raise HTTPException(409, why)
    try:
        path = await ensure_calibration_clip(hub.cfg.paths.data_dir)
    except (RuntimeError, OSError) as e:
        raise HTTPException(500, f"could not render calibration clip: {e}") from e
    try:
        await eng.set_calibration(True, str(path))
    except SetupBusy as e:
        raise HTTPException(409, str(e)) from e
    except SetupUnavailable as e:
        raise HTTPException(503, str(e)) from e
    return {"ok": True, "calibrating": True}


@router.get("/triggers")
def triggers(hub: HubDep) -> dict[str, Any]:
    return {"last_trigger": hub.engine.last_trigger}


@preflight_router.get("")
async def preflight(hub: HubDep) -> dict[str, Any]:
    return await hub.preflight.run()


@preflight_router.post("/test_all")
async def test_all(hub: HubDep) -> dict[str, bool]:
    """Returns once the test has started; the engine restores idle when it ends."""
    eng = hub.engine
    why = eng.setup_blocker()
    if why:
        raise HTTPException(409, why)
    try:
        sound = await ensure_test_sound(hub.cfg.paths.data_dir)
        pattern = await ensure_pattern(hub.cfg.paths.data_dir)
    except (RuntimeError, OSError) as e:
        raise HTTPException(500, f"could not render test media: {e}") from e
    try:
        await eng.run_test_all(str(sound), str(pattern))
    except SetupBusy as e:
        raise HTTPException(409, str(e)) from e
    except SetupUnavailable as e:
        raise HTTPException(503, str(e)) from e
    return {"ok": True, "started": True}
