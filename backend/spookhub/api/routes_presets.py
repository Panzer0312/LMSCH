"""Built-in test presets (see spookhub/presets.py)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from spookhub import presets
from spookhub.api.deps import get_hub
from spookhub.hub import Hub

router = APIRouter(prefix="/api/presets", tags=["presets"])
HubDep = Annotated[Hub, Depends(get_hub)]


@router.get("")
def list_presets() -> list[dict[str, str]]:
    return [
        {"id": p.id, "title": p.title, "detail": p.detail, "kind": p.kind} for p in presets.PRESETS
    ]


@router.post("/remove")
async def remove(hub: HubDep) -> dict[str, Any]:
    notes = await presets.remove_all(hub)
    hub.bus.event("info", "Test presets removed", kind="preset")
    return {"ok": True, "notes": notes}


@router.post("/{preset_id}/apply")
async def apply(preset_id: str, hub: HubDep) -> dict[str, Any]:
    p = presets.BY_ID.get(preset_id)
    if p is None:
        raise HTTPException(404, "unknown preset")
    notes = await p.apply(hub)
    hub.bus.event("info", f"Test preset '{p.title}' applied: {'; '.join(notes)}", kind="preset")
    return {"ok": True, "notes": notes}
