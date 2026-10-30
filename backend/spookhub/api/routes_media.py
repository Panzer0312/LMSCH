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
from spookhub.media import analyze, blackcheck, derive
from spookhub.media.probe import probe
from spookhub.media.transcode import needs_transcode, transcode
from spookhub.media.upload import UploadError, save_upload
from spookhub.models import Cue, Media, Profile
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
    # Clockwise container rotation; width/height are the coded frame.
    rotation: int = 0
    used_by: list[str]
    aspect: float | None = None
    needs_transcode: bool = False
    transcode: dict[str, Any] | None = None
    kind: str = "video"


def _is_audio(m: Media) -> bool:
    return m.kind == "audio"


def _out(m: Media, used_by: list[str], hub: Hub | None = None) -> MediaOut:
    return MediaOut(
        id=m.id or 0,
        filename=m.filename,
        size=m.size,
        duration_s=m.duration_s,
        width=m.width,
        height=m.height,
        codec=m.codec,
        rotation=m.rotation or 0,
        used_by=used_by,
        aspect=display_aspect(m),
        needs_transcode=not _is_audio(m) and needs_transcode(m),
        transcode=hub.transcodes.get(m.id or 0) if hub else None,
        kind=m.kind or "video",
    )


def display_aspect(m: Media) -> float | None:
    """Width/height as the player shows it: coded dims swapped for a 90/270 rotation."""
    if m.width <= 0 or m.height <= 0:
        return None
    w, h = (m.height, m.width) if (m.rotation or 0) % 180 == 90 else (m.width, m.height)
    return round(w / h, 4)


def _users(mid: int) -> list[str]:
    with db.session() as s:
        q = select(Profile).where(
            or_(col(Profile.media_id) == mid, col(Profile.idle_media_id) == mid)
        )
        names = [p.name for p in s.exec(q).all()]
        # Sound cues and ambients reference media inside JSON; few rows, so filter in Python.
        cue_pids = {
            c.profile_id
            for c in s.exec(select(Cue).where(col(Cue.kind) == "sound")).all()
            if (c.sound or {}).get("media_id") == mid
        }
        cue_pids |= {
            p.id or 0
            for p in s.exec(select(Profile)).all()
            if (p.ambient or {}).get("media_id") == mid
        }
        for pid in sorted(cue_pids):
            p = s.get(Profile, pid)
            if p is not None and p.name not in names:
                names.append(p.name)
        return names


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
    if _is_audio(m):
        raise HTTPException(422, "audio files are not transcoded")
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
        row.rotation = info.rotation
        row.duration_s = info.duration_s
        s.add(row)
        s.commit()
    if src != dest:
        src.unlink(missing_ok=True)
    await derive.remove(hub.cfg.paths.media_dir, mid)
    blackcheck.remove(hub.cfg.paths.media_dir, mid)
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
    spawn(
        derive.ensure(
            hub.cfg.paths.media_dir, m.id or 0, Path(m.path), m.duration_s, not _is_audio(m)
        )
    )
    return _out(m, [])


async def _derived(hub: Hub, mid: int, need_video: bool = False) -> dict[str, Path]:
    with db.session() as s:
        m = s.get(Media, mid)
    if m is None or not Path(m.path).exists():
        raise HTTPException(404, "media not found")
    if need_video and _is_audio(m):
        raise HTTPException(404, "thumbnails unavailable")
    await derive.ensure(hub.cfg.paths.media_dir, mid, Path(m.path), m.duration_s, not _is_audio(m))
    return derive.paths(hub.cfg.paths.media_dir, mid)


@router.get("/{mid}/thumbs")
async def thumbs_meta(mid: int, hub: HubDep) -> dict[str, Any]:
    p = await _derived(hub, mid, need_video=True)
    if not p["thumbs"].exists():
        raise HTTPException(404, "thumbnails unavailable")
    return json.loads(p["thumbs"].read_text())


@router.get("/{mid}/thumbs.jpg")
async def thumbs_sprite(mid: int, hub: HubDep) -> FileResponse:
    p = await _derived(hub, mid, need_video=True)
    if not p["sprite"].exists():
        raise HTTPException(404, "thumbnails unavailable")
    return FileResponse(p["sprite"], media_type="image/jpeg")


@router.get("/{mid}/waveform")
async def waveform(mid: int, hub: HubDep) -> dict[str, Any]:
    p = await _derived(hub, mid)
    if not p["wave"].exists():
        return {"rate": 0, "peaks": []}
    return json.loads(p["wave"].read_text())


@router.get("/{mid}/onsets")
async def onsets(mid: int, hub: HubDep) -> dict[str, Any]:
    p = await _derived(hub, mid)  # generates on demand for media uploaded before analysis existed
    if not p["onsets"].exists():
        return {"rate": analyze.ENV_RATE, "onsets": [], "bpm": None}
    return json.loads(p["onsets"].read_text())


@router.get("/{mid}/black_check")
async def black_check(mid: int, hub: HubDep) -> dict[str, Any]:
    with db.session() as s:
        m = s.get(Media, mid)
    if m is None or not Path(m.path).exists():
        raise HTTPException(404, "media not found")
    if _is_audio(m):
        raise HTTPException(422, "audio has no picture to check")
    try:
        return await blackcheck.check(hub.cfg.paths.media_dir, mid, Path(m.path), m.duration_s)
    except TimeoutError as e:  # before OSError, which it subclasses
        raise HTTPException(504, "black check timed out") from e
    except (RuntimeError, OSError) as e:
        raise HTTPException(422, f"black check failed: {e}") from e


@router.get("/{mid}/file")
def file(mid: int) -> FileResponse:
    with db.session() as s:
        m = s.get(Media, mid)
    if m is None or not Path(m.path).exists():
        raise HTTPException(404, "media not found")
    # FileResponse honours Range headers, so the browser preview can seek.
    return FileResponse(m.path, filename=m.filename)


@router.delete("/{mid}")
async def delete(mid: int, hub: HubDep) -> dict[str, bool]:
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
    await derive.remove(hub.cfg.paths.media_dir, mid)
    blackcheck.remove(hub.cfg.paths.media_dir, mid)
    hub.bus.publish("media_changed")
    return {"ok": True}
