"""Setup helpers: output discovery, speaker test, trigger visibility."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.player.devices import drm_outputs
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
    eng = hub.engine
    if eng.playing_main or eng.test_pattern or eng.audition is not None:
        raise HTTPException(409, "a show is playing")
    try:
        path = await ensure_test_sound(hub.cfg.paths.data_dir)
    except (RuntimeError, OSError) as e:
        raise HTTPException(500, f"could not render test sound: {e}") from e
    if not await eng.play_test_sound(str(path)):
        raise HTTPException(409, "a show is playing")
    return {"ok": True}


@router.get("/triggers")
def triggers(hub: HubDep) -> dict[str, Any]:
    return {"last_trigger": hub.engine.last_trigger}
