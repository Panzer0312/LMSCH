"""Display profiles, video setup mode and live geometry (/video page)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from spookhub import db
from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.models import (
    DisplayGeometry,
    DisplayProfile,
    DisplayProfileIn,
    DisplayProfileOut,
    DisplayProfilePatch,
    Media,
    Profile,
)
from spookhub.show import display
from spookhub.show.engine import SetupBusy

router = APIRouter(prefix="/api/display", tags=["display"])
# Media assignment lives here, not in routes_media: it only matters to display resolution.
media_router = APIRouter(prefix="/api/media", tags=["display"])
HubDep = Annotated[Hub, Depends(get_hub)]
PATTERN_SIZE = (1920, 1080)


class PatternSource(BaseModel):
    kind: Literal["pattern"]
    id: str = Field(min_length=1, max_length=64)


class MediaSource(BaseModel):
    kind: Literal["media"]
    id: int


class IdleSource(BaseModel):
    kind: Literal["idle"]


Source = Annotated[PatternSource | MediaSource | IdleSource, Field(discriminator="kind")]


class SetupIn(BaseModel):
    on: bool
    source: Source | None = None


class LiveIn(BaseModel):
    geometry: DisplayGeometry


class SeekIn(BaseModel):
    t: float = Field(ge=0, allow_inf_nan=False)


class MediaDisplayIn(BaseModel):
    display_profile_id: int | None


def _out(p: DisplayProfile) -> DisplayProfileOut:
    return DisplayProfileOut(
        id=p.id or 0, name=p.name, geometry=DisplayGeometry.model_validate(p.geometry or {})
    )


def _get(s: Session, pid: int) -> DisplayProfile:
    p = s.get(DisplayProfile, pid)
    if p is None:
        raise HTTPException(404, "display profile not found")
    return p


def _name_taken(s: Session, name: str, own_id: int | None = None) -> bool:
    other = s.exec(select(DisplayProfile).where(DisplayProfile.name == name)).first()
    return other is not None and other.id != own_id


def _commit(s: Session) -> None:
    try:
        s.commit()
    except IntegrityError as e:  # race between the name check and the insert
        s.rollback()
        raise HTTPException(409, "a display profile with that name exists") from e


def _changed(hub: Hub) -> None:
    hub.bus.publish("display_profiles_changed")
    hub.engine.refresh_display()


@router.get("/profiles")
def list_profiles() -> list[DisplayProfileOut]:
    with db.session() as s:
        rows = s.exec(select(DisplayProfile).order_by(col(DisplayProfile.name))).all()
        return [_out(p) for p in rows]


@router.post("/profiles", status_code=201)
def create_profile(body: DisplayProfileIn, hub: HubDep) -> DisplayProfileOut:
    with db.session() as s:
        if _name_taken(s, body.name):
            raise HTTPException(409, "a display profile with that name exists")
        p = DisplayProfile(name=body.name, geometry=body.geometry.model_dump())
        s.add(p)
        _commit(s)
        s.refresh(p)
        out = _out(p)
    _changed(hub)
    return out


@router.put("/profiles/{pid}")
def update_profile(pid: int, body: DisplayProfilePatch, hub: HubDep) -> DisplayProfileOut:
    with db.session() as s:
        p = _get(s, pid)
        if body.name is not None:
            if _name_taken(s, body.name, pid):
                raise HTTPException(409, "a display profile with that name exists")
            p.name = body.name
        if body.geometry is not None:
            p.geometry = body.geometry.model_dump()
        s.add(p)
        _commit(s)
        out = _out(p)
    _changed(hub)
    return out


@router.delete("/profiles/{pid}")
async def delete_profile(pid: int, hub: HubDep) -> dict[str, bool]:
    with db.session() as s:
        p = _get(s, pid)
        affected: list[int] = []
        for m in s.exec(select(Media).where(Media.display_profile_id == pid)).all():
            m.display_profile_id = None
            s.add(m)
        for pr in s.exec(select(Profile).where(Profile.display_profile_id == pid)).all():
            pr.display_profile_id = None
            affected.append(pr.id or 0)
            s.add(pr)
        s.delete(p)
        s.commit()
    if hub.settings.value.display_profile_id == pid:
        hub.settings.update(display_profile_id=None)
    for prof_id in affected:
        await hub.engine.reload_if_active(prof_id)
    if affected:
        hub.bus.publish("profiles_changed")
    _changed(hub)
    return {"ok": True}


@router.get("/state")
def get_state(hub: HubDep) -> dict[str, Any]:
    return hub.engine.display_state()


async def _source_target(
    hub: Hub, src: PatternSource | MediaSource | IdleSource
) -> tuple[str | None, int | None, tuple[int, int] | None]:
    """(path or None for black, media display profile id, source size)."""
    if isinstance(src, PatternSource):
        from spookhub.show.patterns import ensure as ensure_pattern

        try:
            path = await ensure_pattern(hub.cfg.paths.data_dir, src.id, PATTERN_SIZE)
        except KeyError as e:
            raise HTTPException(404, f"unknown pattern '{src.id}'") from e
        except RuntimeError as e:
            raise HTTPException(500, f"could not render pattern: {e}") from e
        hub.engine.known_sizes[str(path)] = PATTERN_SIZE
        return str(path), None, PATTERN_SIZE
    if isinstance(src, MediaSource):
        found = display.media_by_id(src.id)
        if found is None:
            raise HTTPException(404, "media not found")
        path, info = found
        return path, info.display_profile_id, info.size
    a = hub.engine.active
    idle = a.idle_path if a else None
    info = display.media_by_path(idle)
    return idle, (info.display_profile_id if info else None), (info.size if info else None)


@router.post("/setup")
async def setup(body: SetupIn, hub: HubDep) -> dict[str, Any]:
    eng = hub.engine
    if not body.on:
        await eng.exit_display_setup()
        return eng.display_state()
    src = body.source
    if src is None:
        if eng.display_setup:
            return eng.display_state()
        src = PatternSource(kind="pattern", id="grid")
    # Cheap refusal before a possibly slow pattern render.
    if eng.blackout:
        raise HTTPException(409, "blackout active")
    path, media_dp, size = await _source_target(hub, src)
    try:
        await eng.enter_display_setup(src.model_dump(), path, media_dp, size)
    except SetupBusy as e:
        raise HTTPException(409, str(e)) from e
    return eng.display_state()


@router.post("/live")
def live(body: LiveIn, hub: HubDep) -> dict[str, Any]:
    try:
        hub.engine.set_live(body.geometry)
    except SetupBusy as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/seek")
async def seek(body: SeekIn, hub: HubDep) -> dict[str, bool]:
    try:
        await hub.engine.setup_seek(body.t)
    except SetupBusy as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@media_router.patch("/{mid}")
def set_media_display(mid: int, body: MediaDisplayIn, hub: HubDep) -> dict[str, Any]:
    with db.session() as s:
        m = s.get(Media, mid)
        if m is None:
            raise HTTPException(404, "media not found")
        dp = body.display_profile_id
        if dp is not None and s.get(DisplayProfile, dp) is None:
            raise HTTPException(422, f"display profile {dp} not found")
        m.display_profile_id = dp
        s.add(m)
        s.commit()
    hub.bus.publish("media_changed")
    hub.engine.refresh_display()
    return {"id": mid, "display_profile_id": dp}
