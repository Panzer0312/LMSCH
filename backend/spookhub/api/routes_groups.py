"""Board groups (rooms): CRUD, order, and group-wide quick actions."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import Session, col, select

from spookhub import db
from spookhub.api.deps import get_hub
from spookhub.boards import groups
from spookhub.boards.groups import parse_group_key
from spookhub.hub import Hub
from spookhub.models import (
    BoardGroup,
    BoardGroupMember,
    Cue,
    CueTarget,
    GroupIn,
    GroupOut,
    LightState,
    Profile,
)
from spookhub.tasks import spawn

router = APIRouter(prefix="/api/groups", tags=["groups"])
HubDep = Annotated[Hub, Depends(get_hub)]


def _rows(s: Session) -> list[BoardGroup]:
    return list(s.exec(select(BoardGroup).order_by(col(BoardGroup.sort), col(BoardGroup.id))))


def _members(s: Session, gid: int) -> list[str]:
    q = select(BoardGroupMember).where(BoardGroupMember.group_id == gid)
    return sorted(m.mac for m in s.exec(q))


def _out(s: Session, g: BoardGroup) -> GroupOut:
    gid = g.id or 0
    return GroupOut(id=gid, sort=g.sort, name=g.name, color=g.color, macs=_members(s, gid))


def _get(s: Session, gid: int) -> BoardGroup:
    g = s.get(BoardGroup, gid)
    if g is None:
        raise HTTPException(404, "group not found")
    return g


def _set_members(s: Session, gid: int, macs: list[str], hub: Hub) -> None:
    unknown = [m for m in macs if m not in hub.boards.boards]
    if unknown:
        raise HTTPException(422, f"unknown board {unknown[0]}")
    q = select(BoardGroupMember).where(BoardGroupMember.group_id == gid)
    for m in s.exec(q).all():
        s.delete(m)
    s.flush()
    for mac in dict.fromkeys(macs):
        s.add(BoardGroupMember(group_id=gid, mac=mac))


def _name(s: Session, body: GroupIn, gid: int | None) -> str:
    """Names are the identity on profile import, so they must be unique and not blank."""
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "room name is empty")
    other = s.exec(select(BoardGroup).where(BoardGroup.name == name)).first()
    if other is not None and other.id != gid:
        raise HTTPException(409, f"a room named '{name}' exists")
    return name


def users(s: Session, gid: int) -> list[str]:
    """Profiles whose selection, overrides or cues name the group."""
    out: list[str] = []
    for p in s.exec(select(Profile).order_by(col(Profile.sort), col(Profile.id))).all():
        sel = p.selection or {}
        hit = gid in (sel.get("groups") or []) or any(
            parse_group_key(k) == gid for k in (p.overrides or {})
        )
        if not hit:
            for c in s.exec(select(Cue).where(Cue.profile_id == p.id)).all():
                if gid in CueTarget.model_validate(c.target or {}).groups:
                    hit = True
                    break
        if hit:
            out.append(p.name)
    return out


async def _changed(hub: Hub) -> None:
    hub.bus.publish("groups_changed", groups.snapshot())
    await hub.engine.groups_changed()


@router.get("")
def list_groups() -> list[GroupOut]:
    with db.session() as s:
        return [_out(s, g) for g in _rows(s)]


@router.post("", status_code=201)
async def create(body: GroupIn, hub: HubDep) -> GroupOut:
    with db.session() as s:
        rows = _rows(s)
        g = BoardGroup(
            name=_name(s, body, None),
            color=body.color,
            sort=max((r.sort for r in rows), default=-1) + 1,
        )
        s.add(g)
        s.flush()
        _set_members(s, g.id or 0, body.macs, hub)
        s.commit()
        s.refresh(g)
        out = _out(s, g)
    await _changed(hub)
    return out


@router.put("/order")
async def reorder(ids: Annotated[list[int], Body()], hub: HubDep) -> dict[str, bool]:
    with db.session() as s:
        rows = {g.id: g for g in _rows(s)}
        if len(set(ids)) != len(ids) or set(ids) != set(rows):
            raise HTTPException(422, "order must list every group id exactly once")
        for i, gid in enumerate(ids):
            rows[gid].sort = i
            s.add(rows[gid])
        s.commit()
    # Order decides which group override wins on a shared board.
    await _changed(hub)
    return {"ok": True}


@router.put("/{gid}")
async def update(gid: int, body: GroupIn, hub: HubDep) -> GroupOut:
    with db.session() as s:
        g = _get(s, gid)
        g.name = _name(s, body, gid)
        g.color = body.color
        s.add(g)
        _set_members(s, gid, body.macs, hub)
        s.commit()
        out = _out(s, g)
    await _changed(hub)
    return out


@router.delete("/{gid}")
async def delete(gid: int, hub: HubDep) -> dict[str, bool]:
    with db.session() as s:
        g = _get(s, gid)
        used = users(s, gid)
        if used:
            raise HTTPException(409, f"used by: {', '.join(used)}")
        _set_members(s, gid, [], hub)
        s.delete(g)
        s.commit()
    await _changed(hub)
    return {"ok": True}


@router.put("/{gid}/members/{mac}")
async def add_member(gid: int, mac: str, hub: HubDep) -> GroupOut:
    """Single-board join/leave, so two quick taps on different boards never overwrite each other."""
    if mac not in hub.boards.boards:
        raise HTTPException(422, f"unknown board {mac}")
    with db.session() as s:
        g = _get(s, gid)
        if s.get(BoardGroupMember, (gid, mac)) is None:
            s.add(BoardGroupMember(group_id=gid, mac=mac))
            s.commit()
        out = _out(s, g)
    await _changed(hub)
    return out


@router.delete("/{gid}/members/{mac}")
async def remove_member(gid: int, mac: str, hub: HubDep) -> GroupOut:
    with db.session() as s:
        g = _get(s, gid)
        m = s.get(BoardGroupMember, (gid, mac))
        if m is not None:
            s.delete(m)
            s.commit()
        out = _out(s, g)
    await _changed(hub)
    return out


def _adopted_members(hub: Hub, gid: int) -> list[str]:
    with db.session() as s:
        _get(s, gid)
        macs = _members(s, gid)
    return [m for m in macs if (b := hub.boards.boards.get(m)) is not None and b.adopted]


# async: BoardManager pushes wake asyncio.Events, which must be set on the loop thread.
@router.post("/{gid}/state")
async def group_state(gid: int, body: LightState, hub: HubDep) -> dict[str, Any]:
    """Manual override on every adopted member (like the per-board quick controls)."""
    macs = _adopted_members(hub, gid)
    for mac in macs:
        hub.boards.set_manual(mac, body)
    return {"ok": True, "macs": macs}


@router.delete("/{gid}/state")
async def clear_group_state(gid: int, hub: HubDep) -> dict[str, Any]:
    macs = _adopted_members(hub, gid)
    for mac in macs:
        hub.boards.clear_manual(mac)
    return {"ok": True, "macs": macs}


@router.post("/{gid}/identify")
async def identify(gid: int, hub: HubDep) -> dict[str, Any]:
    macs = _adopted_members(hub, gid)
    for mac in macs:
        spawn(hub.boards.identify(mac))
    return {"ok": True, "macs": macs}
