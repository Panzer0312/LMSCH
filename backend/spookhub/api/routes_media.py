from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import col, or_, select

from spookhub import db
from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.media import derive
from spookhub.media.probe import probe
from spookhub.media.transcode import needs_transcode, transcode
from spookhub.media.upload import UploadError, save_upload
from spookhub.models import Media, Profile
from spookhub.tasks import spawn

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
    needs_transcode: bool = False
    transcode: dict[str, Any] | None = None


def _out(m: Media, used_by: list[str], hub: Hub | None = None) -> MediaOut:
    return MediaOut(
        id=m.id or 0,
        filename=m.filename,
        size=m.size,
        duration_s=m.duration_s,
        width=m.width,
        height=m.height,
        codec=m.codec,
        used_by=used_by,
        needs_transcode=needs_transcode(m),
        transcode=hub.transcodes.get(m.id or 0) if hub else None,
    )


def _users(mid: int) -> list[str]:
    with db.session() as s:
        q = select(Profile).where(
            or_(col(Profile.media_id) == mid, col(Profile.idle_media_id) == mid)
        )
        return [p.name for p in s.exec(q).all()]


@router.get("")
def list_media(hub: HubDep) -> list[MediaOut]:
    with db.session() as s:
        items = s.exec(select(Media).order_by(col(Media.id).desc())).all()
    return [_out(m, _users(m.id or 0), hub) for m in items]


@router.post("/{mid}/transcode", status_code=202)
async def start_transcode(mid: int, hub: HubDep) -> dict[str, bool]:
    with db.session() as s:
        m = s.get(Media, mid)
    if m is None or not Path(m.path).exists():
        raise HTTPException(404, "media not found")
    if hub.transcodes.get(mid, {}).get("state") == "running":
        raise HTTPException(409, "already transcoding")
    hub.transcodes[mid] = {"state": "running", "progress": 0.0, "error": None}
    spawn(_transcode_job(hub, m))
    hub.bus.publish("media_changed")
    return {"ok": True}


async def _transcode_job(hub: Hub, m: Media) -> None:
    mid = m.id or 0
    src = Path(m.path)
    dest = src.with_name(f"{src.stem}-h264.mp4")
    last = 0.0

    def progress(p: float) -> None:
        nonlocal last
        hub.transcodes[mid]["progress"] = p
        if p - last >= 0.01:
            last = p
            hub.bus.publish("transcode", {"id": mid, "progress": round(p, 3)})

    try:
        await transcode(src, dest, m.duration_s, progress)
        info = await probe(dest)
    except Exception as e:  # noqa: BLE001
        hub.transcodes[mid] = {"state": "failed", "progress": 0.0, "error": str(e)}
        hub.bus.event("error", f"Transcode of {m.filename} failed: {e}")
        hub.bus.publish("media_changed")
        return
    # Same row id, so every profile using this video keeps working.
    with db.session() as s:
        row = s.get(Media, mid)
        if row is None:
            dest.unlink(missing_ok=True)
            return
        row.path, row.size = str(dest), dest.stat().st_size
        row.codec, row.width, row.height = info.codec, info.width, info.height
        row.duration_s = info.duration_s
        s.add(row)
        s.commit()
    if src != dest:
        src.unlink(missing_ok=True)
    derive.remove(hub.cfg.paths.media_dir, mid)
    hub.transcodes.pop(mid, None)
    hub.bus.event("info", f"Transcoded {m.filename} to H.264")
    hub.bus.publish("media_changed")
    spawn(derive.ensure(hub.cfg.paths.media_dir, mid, dest, info.duration_s))


@router.post("", status_code=201)
async def upload(file: UploadFile, hub: HubDep) -> MediaOut:
    try:
        m = await save_upload(file, hub.cfg.paths.media_dir)
    except UploadError as e:
        raise HTTPException(422, str(e)) from e
    hub.bus.event("info", f"Uploaded {m.filename}")
    hub.bus.publish("media_changed")
    spawn(derive.ensure(hub.cfg.paths.media_dir, m.id or 0, Path(m.path), m.duration_s))
    return _out(m, [])


async def _derived(hub: Hub, mid: int) -> dict[str, Path]:
    with db.session() as s:
        m = s.get(Media, mid)
    if m is None or not Path(m.path).exists():
        raise HTTPException(404, "media not found")
    await derive.ensure(hub.cfg.paths.media_dir, mid, Path(m.path), m.duration_s)
    return derive.paths(hub.cfg.paths.media_dir, mid)


@router.get("/{mid}/thumbs")
async def thumbs_meta(mid: int, hub: HubDep) -> dict[str, Any]:
    p = await _derived(hub, mid)
    if not p["thumbs"].exists():
        raise HTTPException(404, "thumbnails unavailable")
    return json.loads(p["thumbs"].read_text())


@router.get("/{mid}/thumbs.jpg")
async def thumbs_sprite(mid: int, hub: HubDep) -> FileResponse:
    p = await _derived(hub, mid)
    if not p["sprite"].exists():
        raise HTTPException(404, "thumbnails unavailable")
    return FileResponse(p["sprite"], media_type="image/jpeg")


@router.get("/{mid}/waveform")
async def waveform(mid: int, hub: HubDep) -> dict[str, Any]:
    p = await _derived(hub, mid)
    if not p["wave"].exists():
        return {"rate": 0, "peaks": []}
    return json.loads(p["wave"].read_text())


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
        derive.remove(hub.cfg.paths.media_dir, mid)
        s.delete(m)
        s.commit()
    hub.bus.publish("media_changed")
    return {"ok": True}
