"""Moments: CRUD (locked in show mode), press/stop (allowed in show mode)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from spookhub import db
from spookhub.api.deps import get_hub
from spookhub.hub import Hub
from spookhub.models import Media, Moment, MomentIn, MomentOut, Profile, ProfileType
from spookhub.show.moments import MomentRefused

router = APIRouter(prefix="/api/moments", tags=["moments"])
HubDep = Annotated[Hub, Depends(get_hub)]


class PlayIn(BaseModel):
    variant: int | None = Field(default=None, ge=0)


def _out(m: Moment) -> MomentOut:
    return MomentOut.model_validate(m.model_dump())


def _rows(s: Session) -> list[Moment]:
    return list(s.exec(select(Moment).order_by(Moment.sort, Moment.id)))  # type: ignore[arg-type]


def _check(body: MomentIn, s: Session) -> None:
    """References must exist with the right type: video / light profile, audio media."""

    def profile(pid: int, want: ProfileType, part: str) -> None:
        p = s.get(Profile, pid)
        if p is None:
            raise HTTPException(422, f"{part}: profile {pid} not found")
        if p.type != want:
            raise HTTPException(422, f"{part}: profile '{p.name}' is not a {want.value} profile")

    def audio(mid: int, part: str) -> None:
        m = s.get(Media, mid)
        if m is None:
            raise HTTPException(422, f"{part}: media {mid} not found")
        if m.kind != "audio":
            raise HTTPException(422, f"{part}: media '{m.filename}' is not audio")

    for i, v in enumerate(body.variants, start=1):
        if v.video_profile_id is not None:
            profile(v.video_profile_id, ProfileType.video, f"variant {i} video")
        if v.light_profile_id is not None:
            profile(v.light_profile_id, ProfileType.light, f"variant {i} light")
        if v.sound is not None:
            audio(v.sound.media_id, f"variant {i} sound")
        if v.ambience_media_id is not None:
            audio(v.ambience_media_id, f"variant {i} ambience")


def _apply(m: Moment, body: MomentIn) -> None:
    data = body.model_dump()
    for k in ("name", "icon", "color", "cooldown_s", "busy", "order", "variants"):
        setattr(m, k, data[k])


def _changed(hub: Hub) -> None:
    hub.bus.publish("moments_changed")


@router.get("")
def list_moments() -> list[MomentOut]:
    with db.session() as s:
        return [_out(m) for m in _rows(s)]


@router.get("/state")
def state(hub: HubDep) -> dict[str, Any]:
    return hub.engine.moment_state()


@router.post("", status_code=201)
def create(body: MomentIn, hub: HubDep) -> MomentOut:
    with db.session() as s:
        _check(body, s)
        rows = _rows(s)
        m = Moment(name=body.name, sort=(max((r.sort for r in rows), default=-1) + 1))
        _apply(m, body)
        s.add(m)
        s.commit()
        s.refresh(m)
        out = _out(m)
    _changed(hub)
    return out


@router.put("/order")
def reorder(ids: Annotated[list[int], Body()], hub: HubDep) -> dict[str, bool]:
    with db.session() as s:
        rows = {m.id: m for m in _rows(s)}
        if len(set(ids)) != len(ids) or set(ids) != set(rows):
            raise HTTPException(422, "order must list every moment id exactly once")
        for i, mid in enumerate(ids):
            rows[mid].sort = i
            s.add(rows[mid])
        s.commit()
    _changed(hub)
    return {"ok": True}


@router.post("/stop")
async def stop(hub: HubDep) -> dict[str, Any]:
    return await hub.engine.stop_moment()


@router.put("/{mid}")
def update(mid: int, body: MomentIn, hub: HubDep) -> MomentOut:
    with db.session() as s:
        m = s.get(Moment, mid)
        if m is None:
            raise HTTPException(404, "moment not found")
        _check(body, s)
        _apply(m, body)
        s.add(m)
        s.commit()
        s.refresh(m)
        out = _out(m)
    _changed(hub)
    return out


@router.delete("/{mid}")
def delete(mid: int, hub: HubDep) -> dict[str, bool]:
    with db.session() as s:
        m = s.get(Moment, mid)
        if m is None:
            raise HTTPException(404, "moment not found")
        s.delete(m)
        s.commit()
    _changed(hub)
    return {"ok": True}


@router.post("/{mid}/play")
async def play(
    mid: int, hub: HubDep, body: Annotated[PlayIn | None, Body()] = None
) -> dict[str, Any]:
    try:
        return await hub.engine.play_moment(mid, body.variant if body else None)
    except KeyError as e:
        raise HTTPException(404, "moment not found") from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except MomentRefused as e:
        raise HTTPException(409, str(e)) from e
