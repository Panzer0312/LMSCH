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
    AmbientSpec,
    Cue,
    CueIn,
    CueOut,
    CueTarget,
    DisplayGeometry,
    DisplayProfile,
    LightState,
    Marker,
    Media,
    Moment,
    Profile,
    ProfileIn,
    ProfileOut,
    ProfileType,
    SceneItem,
    SceneSpec,
    SoundSpec,
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
        scene=SceneSpec.model_validate(p.scene) if p.scene is not None else None,
        ambient=AmbientSpec.model_validate(p.ambient) if p.ambient is not None else None,
        fade_ms=p.fade_ms,
        idle_length_s=p.idle_length_s,
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
                kind="sound" if c.kind == "sound" else "light",
                sound=SoundSpec.model_validate(c.sound) if c.kind == "sound" and c.sound else None,
                track="idle" if c.track == "idle" else "main",
            )
            for c in cues
        ],
    )


def _marker_rows(markers: list[Marker]) -> list[dict[str, Any]]:
    ids = [m.id for m in markers]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "duplicate marker id")
    return [m.model_dump() for m in sorted(markers, key=lambda m: m.t)]


def _check_scene(body: ProfileIn, s: Session, pid: int | None) -> None:
    if body.type != ProfileType.scene:
        return
    sc = body.scene
    if sc is None:
        raise HTTPException(422, "scene profile needs a scene")
    ids = [i.profile_id for i in sc.items]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "scene lists a profile twice")
    refs = ids + ([sc.idle_profile_id] if sc.idle_profile_id is not None else [])
    for ref in refs:
        if ref == pid:
            raise HTTPException(422, "a scene cannot contain itself")
        row = s.get(Profile, ref)
        if row is None:
            raise HTTPException(422, f"profile {ref} not found")
        if row.type == ProfileType.scene:
            raise HTTPException(422, "scenes cannot contain scenes")


def _check_refs(s: Session, pid: int, new_type: ProfileType) -> None:
    """A type change must not break scenes or moments that use this profile."""
    if new_type == ProfileType.scene:
        for sc in s.exec(select(Profile).where(Profile.type == ProfileType.scene)).all():
            spec = SceneSpec.model_validate(sc.scene or {})
            if spec.idle_profile_id == pid or any(i.profile_id == pid for i in spec.items):
                raise HTTPException(422, f"used by scene '{sc.name}'; scenes cannot contain scenes")
    for m in s.exec(select(Moment)).all():
        for v in m.variants or []:
            if v.get("video_profile_id") == pid and new_type != ProfileType.video:
                raise HTTPException(422, f"used as video by moment '{m.name}'")
            if v.get("light_profile_id") == pid and new_type != ProfileType.light:
                raise HTTPException(422, f"used as light by moment '{m.name}'")


def _apply(p: Profile, body: ProfileIn, s: Session) -> None:
    _check_scene(body, s, p.id)
    if p.id is not None and body.type != p.type:
        _check_refs(s, p.id, body.type)
    for mid in (body.media_id, body.idle_media_id):
        if mid is None:
            continue
        m = s.get(Media, mid)
        if m is None:
            raise HTTPException(422, f"media {mid} not found")
        if m.kind == "audio" and body.type == ProfileType.video:
            raise HTTPException(422, f"media {mid} is audio; a video profile needs a video")
    if body.type == ProfileType.video and body.media_id is None:
        raise HTTPException(422, "video profile needs a video")
    if body.ambient is not None:
        m = s.get(Media, body.ambient.media_id)
        if m is None or m.kind != "audio":
            raise HTTPException(422, f"ambient media {body.ambient.media_id} is not an audio file")
    # Omitted = untouched: clients that predate display profiles must not clear the override.
    if "display_profile_id" in body.model_fields_set:
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
    p.scene = body.scene.model_dump() if body.type == ProfileType.scene and body.scene else None
    p.ambient = body.ambient.model_dump() if body.ambient else None
    p.fade_ms = body.fade_ms
    p.idle_length_s = body.idle_length_s if body.type == ProfileType.light else None
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
    dropped = 0
    with db.session() as s:
        p = _get(s, pid)
        to_light = p.type != ProfileType.light and body.type == ProfileType.light
        _apply(p, body, s)
        if to_light:
            # A light profile only has an idle track; left-over main cues would make every
            # later cue save fail, and no light editor shows them to delete.
            for c in s.exec(select(Cue).where(Cue.profile_id == pid)).all():
                if c.track != "idle":
                    s.delete(c)
                    dropped += 1
        s.add(p)
        s.commit()
        out = to_out(s, p)
    if dropped:
        hub.bus.event(
            "info", f"Profile '{out.name}' is now a light profile: {dropped} main cues removed"
        )
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
        touched = _drop_from_scenes(s, pid)
        moments = _drop_from_moments(s, pid)
        s.commit()
    engine = hub.engine
    await engine.profile_deleted(pid)
    for sid in touched:
        await engine.reload_if_active(sid)
    _changed(hub)
    if moments:
        hub.bus.publish("moments_changed")
    return {"ok": True}


def _drop_from_moments(s: Session, pid: int) -> bool:
    """Strip a deleted profile from moment variants; a variant left without parts goes, the
    moment stays (possibly with no variants until edited)."""
    changed = False
    for m in s.exec(select(Moment)).all():
        variants = []
        for v in m.variants or []:
            v = dict(v)
            for key in ("video_profile_id", "light_profile_id"):
                if v.get(key) == pid:
                    v[key] = None
            if v.get("video_profile_id") or v.get("light_profile_id") or v.get("sound"):
                variants.append(v)
        if variants != (m.variants or []):
            m.variants = variants
            s.add(m)
            changed = True
    return changed


def _drop_from_scenes(s: Session, pid: int) -> list[int]:
    """Remove a deleted profile from every scene's items / idle part. Returns touched ids."""
    touched = []
    for sc in s.exec(select(Profile).where(Profile.type == ProfileType.scene)).all():
        spec = SceneSpec.model_validate(sc.scene or {})
        items = [i for i in spec.items if i.profile_id != pid]
        idle = None if spec.idle_profile_id == pid else spec.idle_profile_id
        if len(items) != len(spec.items) or idle != spec.idle_profile_id:
            spec = spec.model_copy(update={"items": items, "idle_profile_id": idle})
            sc.scene = spec.model_dump()
            s.add(sc)
            touched.append(sc.id or 0)
    return touched


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
        kind=c.kind,
        sound=c.sound.model_dump() if c.sound else None,
        track=c.track,
    )


@router.put("/{pid}/cues")
async def replace_cues(pid: int, cues: list[CueIn], hub: HubDep) -> ProfileOut:
    """Replace the whole cue list (list-form editor saves atomically)."""
    with db.session() as s:
        p = _get(s, pid)
        if p.type == ProfileType.scene:
            raise HTTPException(422, "scenes have no cues")
        # A light profile has no main video: only its looping idle track.
        if p.type == ProfileType.light and any(c.track != "idle" for c in cues):
            raise HTTPException(422, "light profiles only have idle cues")
        for c in cues:
            if c.sound is not None and s.get(Media, c.sound.media_id) is None:
                raise HTTPException(422, f"sound media {c.sound.media_id} not found")
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
        scene = _scene_export(s, p.scene) if p.scene else None
        amb = s.get(Media, p.ambient.media_id) if p.ambient else None
        cues = []
        for c in p.cues:
            row = c.model_dump(exclude={"id"})
            if c.sound is not None:
                # Media ids differ between hubs: the filename is the identity, like videos.
                snd = s.get(Media, c.sound.media_id)
                row["sound"]["media_filename"] = snd.filename if snd else None
            cues.append(row)
    data = {
        "format": EXPORT_FORMAT,
        "version": 1,
        "profile": p.model_dump(
            exclude={
                "id",
                "sort",
                "cues",
                "media_id",
                "idle_media_id",
                "display_profile_id",
                "scene",
                "ambient",
            }
        ),
        "cues": cues,
        "media_filename": media.filename if media else None,
        "idle_media_filename": idle.filename if idle else None,
        # Ids differ between hubs; the name is the identity, geometry recreates it if missing.
        "display_profile": None if dp is None else {"name": dp.name, "geometry": dp.geometry},
        # Scene items by profile name, like videos by filename.
        "scene": scene,
        "ambient": None
        if p.ambient is None or amb is None
        else {**p.ambient.model_dump(exclude={"media_id"}), "media_filename": amb.filename},
    }
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in p.name)[:40] or "profile"
    return JSONResponse(
        data, headers={"Content-Disposition": f'attachment; filename="{safe}.spookhub.json"'}
    )


def _scene_export(s: Session, sc: SceneSpec) -> dict[str, Any]:
    def name(pid: int | None) -> str | None:
        row = s.get(Profile, pid) if pid is not None else None
        return row.name if row else None

    return {
        "order": sc.order,
        "chain": sc.chain,
        "light_hold_s": sc.light_hold_s,
        "idle_profile": name(sc.idle_profile_id),
        "items": [{"name": name(i.profile_id), "weight": i.weight} for i in sc.items],
    }


def _scene_import(s: Session, data: Any) -> SceneSpec:
    """Match items by profile name on this hub; missing ones are skipped."""
    data = data if isinstance(data, dict) else {}
    by_name: dict[str, int] = {}
    for row in s.exec(select(Profile).order_by(col(Profile.id))).all():
        if row.type != ProfileType.scene and row.id is not None:
            by_name.setdefault(row.name, row.id)
    items: list[SceneItem] = []
    for it in data.get("items") or []:
        pid = by_name.get(str(it.get("name"))) if isinstance(it, dict) else None
        if pid is not None and all(i.profile_id != pid for i in items):
            items.append(SceneItem(profile_id=pid, weight=it.get("weight", 1.0)))
    return SceneSpec(
        items=items,
        order=data.get("order", "shuffle"),
        idle_profile_id=by_name.get(str(data.get("idle_profile"))),
        chain=bool(data.get("chain", False)),
        light_hold_s=data.get("light_hold_s", 10.0),
    )


def _ambient_import(s: Session, data: Any) -> AmbientSpec | None:
    """Ambient sound by filename; a missing (or non-audio) file drops the ambient."""
    if not isinstance(data, dict):
        return None
    mid = _media_by_name(s, data.get("media_filename"))
    m = s.get(Media, mid) if mid is not None else None
    if m is None or m.kind != "audio":
        return None
    try:
        return AmbientSpec.model_validate({**data, "media_id": mid})
    except ValidationError:
        return None


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
        raw_cues = [c for c in d.get("cues", []) if isinstance(c, dict)]
        cues = [CueIn.model_validate(c) for c in raw_cues]
        dp_data = d.get("display_profile")
        dp_name: str | None = None
        dp_geom: DisplayGeometry | None = None
        if isinstance(dp_data, dict) and dp_data.get("name"):
            dp_name = str(dp_data["name"])[:100]
            dp_geom = DisplayGeometry.model_validate(dp_data.get("geometry") or {})
    except ValidationError as e:
        raise HTTPException(422, f"invalid profile data: {e.errors()[0]['msg']}") from e
    created_dp = False
    warnings: list[str] = []
    with db.session() as s:
        if prof.type == ProfileType.scene:
            try:
                prof.scene = _scene_import(s, d.get("scene"))
            except ValidationError as e:
                raise HTTPException(422, f"invalid scene data: {e.errors()[0]['msg']}") from e
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
        prof.ambient = _ambient_import(s, d.get("ambient"))
        amb_name = (d.get("ambient") or {}).get("media_filename")
        if prof.ambient is None and amb_name:
            warnings.append(f"ambient sound '{amb_name}' not found, ambient dropped")
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
        for raw, c in zip(raw_cues, cues, strict=True):
            if c.sound is not None:
                fname = (raw.get("sound") or {}).get("media_filename")
                mid = _media_by_name(s, fname)
                if mid is None:
                    # Never fall back to the raw id: on another hub it is some other file.
                    warnings.append(
                        f"sound '{fname or c.sound.media_id}' not found, "
                        f"sound cue at {c.start_s:g} s dropped"
                    )
                    continue
                c.sound.media_id = mid
            if not c.target.all:
                c.target.macs = [m for m in c.target.macs if m in known]
                if not c.target.macs:
                    continue
            s.add(_cue_row(p.id or 0, c))
        s.commit()
        out = to_out(s, p)
    if created_dp:
        hub.bus.publish("display_profiles_changed")
    # The import response is the profile itself; dropped parts go to the event log.
    for w in warnings:
        hub.bus.event("warn", f"Import '{out.name}': {w}", kind="import")
    _changed(hub)
    return out
