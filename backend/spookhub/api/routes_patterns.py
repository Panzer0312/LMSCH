"""Alignment pattern catalogue; mounted next to `routes_display` under the same prefix."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.show.patterns import PATTERNS, Kind, ensure

router = APIRouter(prefix="/api/display", tags=["display"])
HubDep = Annotated[Hub, Depends(get_hub)]


class PatternOut(BaseModel):
    id: str
    label: str
    kind: Kind


@router.get("/patterns")
def patterns() -> list[PatternOut]:
    return [PatternOut(id=p.id, label=p.label, kind=p.kind) for p in PATTERNS]


@router.get("/patterns/{pid}/file")
async def pattern_file(pid: str, hub: HubDep) -> FileResponse:
    try:
        path = await ensure(hub.cfg.paths.data_dir, pid)
    except KeyError:
        raise HTTPException(404, "unknown pattern") from None
    except RuntimeError as e:  # ffmpeg render failed (stderr tail in the message)
        raise HTTPException(503, f"pattern render failed: {e}") from None
    media_type = "video/mp4" if path.suffix == ".mp4" else "image/png"
    return FileResponse(path, media_type=media_type)
