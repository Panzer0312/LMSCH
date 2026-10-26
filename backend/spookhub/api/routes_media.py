from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import col, or_, select

from spookhub import db
from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.media.upload import UploadError, save_upload
from spookhub.models import Media, Profile

router = APIRouter(prefix="/api/media", tags=["media"])
HubDep = Annotated[Hub, Depends(get_hub)]


class MediaOut(BaseModel):
    id: int
    filename: str
    size: int
    duration_s: float
    width: int
    height: int
    codec: str
    used_by: list[str]


def _out(m: Media, used_by: list[str]) -> MediaOut:
    return MediaOut(
        id=m.id or 0,
        filename=m.filename,
        size=m.size,
        duration_s=m.duration_s,
        width=m.width,
        height=m.height,
        codec=m.codec,
        used_by=used_by,
    )


def _users(mid: int) -> list[str]:
    with db.session() as s:
        q = select(Profile).where(
            or_(col(Profile.media_id) == mid, col(Profile.idle_media_id) == mid)
        )
        return [p.name for p in s.exec(q).all()]


@router.get("")
def list_media() -> list[MediaOut]:
    with db.session() as s:
        items = s.exec(select(Media).order_by(col(Media.id).desc())).all()
    return [_out(m, _users(m.id or 0)) for m in items]


@router.post("", status_code=201)
async def upload(file: UploadFile, hub: HubDep) -> MediaOut:
    try:
        m = await save_upload(file, hub.cfg.paths.media_dir)
    except UploadError as e:
        raise HTTPException(422, str(e)) from e
    hub.bus.event("info", f"Uploaded {m.filename}")
    hub.bus.publish("media_changed")
    return _out(m, [])


@router.get("/{mid}/file")
def file(mid: int) -> FileResponse:
    with db.session() as s:
        m = s.get(Media, mid)
    if m is None or not Path(m.path).exists():
        raise HTTPException(404, "media not found")
    # FileResponse honours Range headers, so the browser preview can seek.
    return FileResponse(m.path, filename=m.filename)


@router.delete("/{mid}")
def delete(mid: int, hub: HubDep) -> dict[str, bool]:
    users = _users(mid)
    if users:
        raise HTTPException(409, f"used by: {', '.join(users)}")
    with db.session() as s:
        m = s.get(Media, mid)
        if m is None:
            raise HTTPException(404, "media not found")
        Path(m.path).unlink(missing_ok=True)
        s.delete(m)
        s.commit()
    hub.bus.publish("media_changed")
    return {"ok": True}
