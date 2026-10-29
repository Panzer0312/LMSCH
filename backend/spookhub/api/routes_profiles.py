from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, col, select

from spookhub import db
from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.models import (
    Cue,
    CueIn,
    CueOut,
    CueTarget,
    DisplayGeometry,
    DisplayProfile,
    LightState,
    Marker,
    Media,
    Profile,
    ProfileIn,
    ProfileOut,
    ProfileType,
)

router = APIRouter(prefix="/api/profiles", tags=["profiles"])
HubDep = Annotated[Hub, Depends(get_hub)]


def to_out(s: Session, p: Profile) -> ProfileOut:
    cues = s.exec(
        select(Cue).where(Cue.profile_id == p.id).order_by(col(Cue.start_s), col(Cue.id))
    ).all()
    return ProfileOut(
        id=p.id or 0,
        sort=p.sort,
        name=p.name,
        type=p.type,
        selection=p.selection,  # type: ignore[arg-type]
        base_state=LightState.model_validate(p.base_state or {}),
        overrides={k: LightState.model_validate(v) for k, v in (p.overrides or {}).items()},
        media_id=p.media_id,
        idle_media_id=p.idle_media_id,
        playback_mode=p.playback_mode,
        cooldown_s=p.cooldown_s,
        markers=[Marker.model_validate(m) for m in (p.markers or [])],
        display_profile_id=p.display_profile_id,
        cues=[
            CueOut(
                id=c.id or 0,
                start_s=c.start_s,
                duration_s=c.duration_s,
                target=CueTarget.model_validate(c.target or {}),
                state=LightState.model_validate(c.state or {}),
                transition_ms=c.transition_ms,
                label=c.label,
                color=c.color,
            )
            for c in cues
        ],
    )


def _marker_rows(markers: list[Marker]) -> list[dict[str, Any]]:
    ids = [m.id for m in markers]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "duplicate marker id")
    return [m.model_dump() for m in sorted(markers, key=lambda m: m.t)]


def _apply(p: Profile, body: ProfileIn, s: Session) -> None:
    for mid in (body.media_id, body.idle_media_id):
        if mid is not None and s.get(Media, mid) is None:
            raise HTTPException(422, f"media {mid} not found")
    if body.type == ProfileType.video and body.media_id is None:
        raise HTTPException(422, "video profile needs a video")
    dp = body.display_profile_id
    if dp is not None and s.get(DisplayProfile, dp) is None:
        raise HTTPException(422, f"display profile {dp} not found")
    p.display_profile_id = dp
    p.name = body.name
    p.type = body.type
    p.selection = body.selection.model_dump()
    p.base_state = body.base_state.model_dump(exclude_none=True)
    p.overrides = {k: v.model_dump(exclude_none=True) for k, v in body.overrides.items()}
    p.media_id = body.media_id if body.type == ProfileType.video else None
    p.idle_media_id = body.idle_media_id if body.type == ProfileType.video else None
    p.playback_mode = body.playback_mode
    p.cooldown_s = body.cooldown_s
    if body.markers is not None:
        p.markers = _marker_rows(body.markers)


def _get(s: Session, pid: int) -> Profile:
    p = s.get(Profile, pid)
    if p is None:
        raise HTTPException(404, "profile not found")
    return p


def _changed(hub: Hub) -> None:
    hub.bus.publish("profiles_changed")


@router.get("")
def list_profiles() -> list[ProfileOut]:
    with db.session() as s:
        ps = s.exec(select(Profile).order_by(col(Profile.sort), col(Profile.id))).all()
        return [to_out(s, p) for p in ps]


@router.post("", status_code=201)
def create(body: ProfileIn, hub: HubDep) -> ProfileOut:
    with db.session() as s:
        last = s.exec(select(Profile).order_by(col(Profile.sort).desc())).first()
        p = Profile(name=body.name, sort=(last.sort + 1) if last else 0)
        _apply(p, body, s)
        s.add(p)
        s.commit()
        s.refresh(p)
        out = to_out(s, p)
    _changed(hub)
    return out


@router.get("/{pid}")
def get(pid: int) -> ProfileOut:
    with db.session() as s:
        return to_out(s, _get(s, pid))


@router.put("/{pid}")
async def update(pid: int, body: ProfileIn, hub: HubDep) -> ProfileOut:
    with db.session() as s:
        p = _get(s, pid)
        _apply(p, body, s)
        s.add(p)
        s.commit()
        out = to_out(s, p)
    await hub.engine.reload_if_active(pid)
    _changed(hub)
    return out


@router.delete("/{pid}")
async def delete(pid: int, hub: HubDep) -> dict[str, bool]:
    with db.session() as s:
        p = _get(s, pid)
        for c in s.exec(select(Cue).where(Cue.profile_id == pid)).all():
            s.delete(c)
        s.delete(p)
        s.commit()
    if hub.engine.active and hub.engine.active.id == pid:
        await hub.engine.deactivate()
    _changed(hub)
    return {"ok": True}


@router.post("/{pid}/duplicate", status_code=201)
def duplicate(pid: int, hub: HubDep) -> ProfileOut:
    with db.session() as s:
        src = to_out(s, _get(s, pid))
        body = ProfileIn.model_validate(src.model_dump(exclude={"id", "sort", "cues"}))
        body.name = f"{src.name} (copy)"
        last = s.exec(select(Profile).order_by(col(Profile.sort).desc())).first()
        p = Profile(name=body.name, sort=(last.sort + 1) if last else 0)
        _apply(p, body, s)
        s.add(p)
        s.commit()
        s.refresh(p)
        for c in src.cues:
            s.add(_cue_row(p.id or 0, c))
        s.commit()
        out = to_out(s, p)
    _changed(hub)
    return out


@router.post("/{pid}/activate")
async def activate(pid: int, hub: HubDep) -> dict[str, bool]:
    try:
        await hub.engine.activate(pid)
    except KeyError as e:
        raise HTTPException(404, "profile not found") from e
    return {"ok": True}


def _cue_row(pid: int, c: CueIn) -> Cue:
    return Cue(
        profile_id=pid,
        start_s=c.start_s,
        duration_s=c.duration_s,
        target=c.target.model_dump(),
        state=c.state.model_dump(exclude_none=True),
        transition_ms=c.transition_ms,
        label=c.label,
        color=c.color,
    )


@router.put("/{pid}/cues")
async def replace_cues(pid: int, cues: list[CueIn], hub: HubDep) -> ProfileOut:
    """Replace the whole cue list (list-form editor saves atomically)."""
    with db.session() as s:
        p = _get(s, pid)
        if p.type != ProfileType.video:
            raise HTTPException(422, "only video profiles have cues")
        for c in s.exec(select(Cue).where(Cue.profile_id == pid)).all():
            s.delete(c)
        for c in cues:
            s.add(_cue_row(pid, c))
        s.commit()
        out = to_out(s, p)
    await hub.engine.reload_if_active(pid)
    _changed(hub)
    return out


@router.put("/{pid}/markers")
def replace_markers(pid: int, markers: list[Marker], hub: HubDep) -> ProfileOut:
    rows = _marker_rows(markers)
    with db.session() as s:
        p = _get(s, pid)
        p.markers = rows
        s.add(p)
        s.commit()
        out = to_out(s, p)
    _changed(hub)
    return out


EXPORT_FORMAT = "spookhub-profile"


@router.get("/{pid}/export")
def export_profile(pid: int) -> JSONResponse:
    """Profile + cues as JSON. Videos are referenced by filename only (files are not included)."""
    with db.session() as s:
        p = to_out(s, _get(s, pid))
        media = s.get(Media, p.media_id) if p.media_id else None
        idle = s.get(Media, p.idle_media_id) if p.idle_media_id else None
        dp = s.get(DisplayProfile, p.display_profile_id) if p.display_profile_id else None
    data = {
        "format": EXPORT_FORMAT,
        "version": 1,
        "profile": p.model_dump(
            exclude={"id", "sort", "cues", "media_id", "idle_media_id", "display_profile_id"}
        ),
        "cues": [c.model_dump(exclude={"id"}) for c in p.cues],
        "media_filename": media.filename if media else None,
        "idle_media_filename": idle.filename if idle else None,
        # Ids differ between hubs; the name is the identity, geometry recreates it if missing.
        "display_profile": None if dp is None else {"name": dp.name, "geometry": dp.geometry},
    }
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in p.name)[:40] or "profile"
    return JSONResponse(
        data, headers={"Content-Disposition": f'attachment; filename="{safe}.spookhub.json"'}
    )


class ImportIn(BaseModel):
    data: dict[str, Any]
    media_id: int | None = None  # overrides filename matching
    idle_media_id: int | None = None


def _media_by_name(s: Session, name: str | None) -> int | None:
    if not name:
        return None
    m = s.exec(select(Media).where(Media.filename == name).order_by(col(Media.id).desc())).first()
    return m.id if m else None


@router.post("/import", status_code=201)
def import_profile(body: ImportIn, hub: HubDep) -> ProfileOut:
    d = body.data
    if d.get("format") != EXPORT_FORMAT:
        raise HTTPException(422, "not a Spookhub profile export")
    try:
        prof = ProfileIn.model_validate(d.get("profile", {}))
        cues = [CueIn.model_validate(c) for c in d.get("cues", [])]
        dp_data = d.get("display_profile")
        dp_name: str | None = None
        dp_geom: DisplayGeometry | None = None
        if isinstance(dp_data, dict) and dp_data.get("name"):
            dp_name = str(dp_data["name"])[:100]
            dp_geom = DisplayGeometry.model_validate(dp_data.get("geometry") or {})
    except ValidationError as e:
        raise HTTPException(422, f"invalid profile data: {e.errors()[0]['msg']}") from e
    created_dp = False
    with db.session() as s:
        if prof.type == ProfileType.video:
            prof.media_id = body.media_id or _media_by_name(s, d.get("media_filename"))
            prof.idle_media_id = body.idle_media_id or _media_by_name(
                s, d.get("idle_media_filename")
            )
            if prof.media_id is None:
                raise HTTPException(
                    422, f"video '{d.get('media_filename')}' not found; choose a video to use"
                )
        prof.display_profile_id = None  # a foreign hub's id means nothing here
        if dp_name is not None and dp_geom is not None:
            # Same name = same physical setup on this hub: reuse it, never overwrite it.
            row = s.exec(select(DisplayProfile).where(DisplayProfile.name == dp_name)).first()
            if row is None:
                row = DisplayProfile(name=dp_name, geometry=dp_geom.model_dump())
                s.add(row)
                s.flush()  # id now, committed together with the profile
                created_dp = True
            prof.display_profile_id = row.id
        # Board-specific targets only make sense on the same boards; unknown MACs are dropped.
        known = set(hub.boards.boards)
        prof.selection.macs = [m for m in prof.selection.macs if m in known]
        prof.overrides = {k: v for k, v in prof.overrides.items() if k in known}
        last = s.exec(select(Profile).order_by(col(Profile.sort).desc())).first()
        p = Profile(name=prof.name, sort=(last.sort + 1) if last else 0)
        _apply(p, prof, s)
        s.add(p)
        s.commit()
        s.refresh(p)
        for c in cues:
            if not c.target.all:
                c.target.macs = [m for m in c.target.macs if m in known]
                if not c.target.macs:
                    continue
            s.add(_cue_row(p.id or 0, c))
        s.commit()
        out = to_out(s, p)
    if created_dp:
        hub.bus.publish("display_profiles_changed")
    _changed(hub)
    return out
