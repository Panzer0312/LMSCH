"""Setup helpers: output discovery, speaker test, trigger visibility."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.player.devices import drm_outputs
from spookhub.show.engine import SetupBusy, SetupUnavailable
from spookhub.show.sounds import ensure_test_sound

router = APIRouter(prefix="/api/setup", tags=["setup"])
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


@router.get("/triggers")
def triggers(hub: HubDep) -> dict[str, Any]:
    return {"last_trigger": hub.engine.last_trigger}
