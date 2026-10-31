"""Board groups (rooms): one resolver from selections, cue targets and overrides to MACs.

Groups are resolved when a profile loads (engine, moments, scenes, fallbacks all go through
load_active), never copied into what is saved. Editing a group reloads the active profile, so
membership changes apply live without touching the profiles or cues that use the group.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlmodel import Session, col, select

from spookhub import db
from spookhub.models import (
    GROUP_KEY,
    BoardGroup,
    BoardGroupMember,
    BoardSelection,
    CueTarget,
    LightState,
)

# group id -> member MACs, in group sort order (dicts keep insertion order).
Groups = Mapping[int, list[str]]


def load_groups(s: Session) -> dict[int, list[str]]:
    rows = s.exec(select(BoardGroup).order_by(col(BoardGroup.sort), col(BoardGroup.id))).all()
    out: dict[int, list[str]] = {g.id: [] for g in rows if g.id is not None}
    for m in s.exec(select(BoardGroupMember).order_by(col(BoardGroupMember.mac))).all():
        if m.group_id in out:
            out[m.group_id].append(m.mac)
    return out


def snapshot() -> list[dict[str, Any]]:
    """Groups as the UI sees them (GroupOut shape), for the WS snapshot and groups_changed."""
    with db.session() as s:
        rows = s.exec(select(BoardGroup).order_by(col(BoardGroup.sort), col(BoardGroup.id))).all()
        members = load_groups(s)
        return [
            {"id": g.id, "name": g.name, "color": g.color, "sort": g.sort, "macs": members[g.id]}
            for g in rows
            if g.id is not None
        ]


def group_macs(ids: Iterable[int], groups: Groups) -> list[str]:
    """Members of the given groups, deduplicated; unknown ids contribute nothing."""
    seen: dict[str, None] = {}
    for gid in ids:
        for mac in groups.get(gid, ()):
            seen.setdefault(mac, None)
    return list(seen)


def resolve_selection(sel: BoardSelection, groups: Groups) -> BoardSelection:
    """Explicit MACs plus group members, groups cleared."""
    if sel.all or not sel.groups:
        return sel.model_copy(update={"groups": []})
    macs = list(dict.fromkeys([*sel.macs, *group_macs(sel.groups, groups)]))
    return BoardSelection(all=False, macs=macs, groups=[])


def resolve_targets(tgt: CueTarget, groups: Groups) -> frozenset[str] | None:
    """Whole-board MACs a cue hits; None = all selected boards. An unknown or empty group
    makes the cue hit nothing (preflight warns)."""
    if tgt.all:
        return None
    return frozenset(tgt.macs) | frozenset(group_macs(tgt.groups, groups))


def resolve_overrides(raw: Mapping[str, Any], groups: Groups) -> dict[str, LightState]:
    """`group:<id>` keys expand to their members. Board keys beat group keys; between groups
    the later group in sort order wins (it is merged on top)."""
    out: dict[str, LightState] = {}
    for gid, members in groups.items():
        st = raw.get(f"{GROUP_KEY}{gid}")
        if st is None:
            continue
        ls = LightState.model_validate(st)
        for mac in members:
            out[mac] = out[mac].merged(ls) if mac in out else ls
    for key, st in raw.items():
        if not key.startswith(GROUP_KEY):
            ls = LightState.model_validate(st)
            out[key] = out[key].merged(ls) if key in out else ls
    return out


def parse_group_key(key: str) -> int | None:
    if not key.startswith(GROUP_KEY):
        return None
    try:
        return int(key[len(GROUP_KEY) :])
    except ValueError:
        return None
