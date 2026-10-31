"""Built-in test presets: ready-made light profiles, rooms, a scene, a moment and settings
bundles so a fresh hub can be checked on real boards without building a show first.

Everything a preset creates is named with the `[Test]` prefix and found again by that name, so
applying twice updates in place and "remove" never touches the user's own items. Creation goes
through the normal route functions, so validation, WS pushes and live reloads stay the same.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlmodel import col, select

from spookhub import db
from spookhub.models import (
    BoardGroup,
    CueIn,
    GroupIn,
    Moment,
    MomentIn,
    Profile,
    ProfileIn,
    ProfileType,
)

if TYPE_CHECKING:
    from spookhub.hub import Hub

PREFIX = "[Test] "

# WLED built-in effect ids (stable across 0.13–0.15).
FX_SOLID, FX_BREATHE, FX_RAINBOW, FX_CANDLE = 0, 2, 9, 88

WARM = (255, 170, 80)
WHITE = (255, 255, 255)
RED = (200, 0, 0)
YELLOW = (255, 190, 0)
STORM = (20, 30, 120)


@dataclass(frozen=True)
class Preset:
    id: str
    title: str
    detail: str
    kind: str  # 'show' (creates items) | 'settings'
    apply: Callable[[Hub], Awaitable[list[str]]]


def _find(model: Any, name: str) -> int | None:
    with db.session() as s:
        row = s.exec(select(model).where(model.name == name)).first()
        return row.id if row else None


async def _upsert_profile(hub: Hub, body: ProfileIn, cues: list[CueIn] | None = None) -> int:
    from spookhub.api import routes_profiles as rp

    body.name = PREFIX + body.name
    pid = _find(Profile, body.name)
    if pid is None:
        pid = rp.create(body, hub).id
    else:
        await rp.update(pid, body, hub)
    if cues is not None:
        await rp.replace_cues(pid, cues, hub)
    return pid


async def _upsert_group(hub: Hub, name: str, color: str, macs: list[str]) -> int:
    from spookhub.api import routes_groups as rg

    body = GroupIn(name=PREFIX + name, color=color, macs=macs)
    gid = _find(BoardGroup, body.name)
    if gid is None:
        return (await rg.create(body, hub)).id
    await rg.update(gid, body, hub)
    return gid


def _solid(rgb: tuple[int, int, int], bri: int = 128, fx: int = FX_SOLID, **extra: Any) -> Any:
    return {"on": True, "bri": bri, "fx": fx, "colors": [rgb], **extra}


def _light(name: str, state: Any, idle_s: float | None = None) -> ProfileIn:
    return ProfileIn.model_validate(
        {"name": name, "type": "light", "base_state": state, "idle_length_s": idle_s}
    )


def _flash(t: float, dur: float, target: Any = None) -> CueIn:
    return CueIn.model_validate(
        {
            "start_s": t,
            "duration_s": dur,
            "track": "idle",
            "transition_ms": 0,
            "target": target or {"all": True},
            "state": {"on": True, "bri": 255, "fx": FX_SOLID, "colors": [WHITE]},
        }
    )


async def _looks(hub: Hub) -> list[str]:
    """Static looks: every board should show the same thing at once."""
    await _upsert_profile(hub, _light("Warm white", _solid(WARM)))
    await _upsert_profile(hub, _light("Rainbow", _solid(WHITE, fx=FX_RAINBOW)))
    await _upsert_profile(hub, _light("Candle flicker", _solid(WARM, fx=FX_CANDLE)))
    await _upsert_profile(hub, _light("Blood pulse", _solid(RED, fx=FX_BREATHE, sx=90)))
    return ["4 light profiles: Warm white, Rainbow, Candle flicker, Blood pulse"]


async def _lightning(hub: Hub) -> list[str]:
    """Idle loop with short white flashes: shows cue timing and transition 0 on the boards."""
    cues = [_flash(2.0, 0.08), _flash(2.2, 0.15), _flash(6.5, 0.1), _flash(6.75, 0.3)]
    await _upsert_profile(hub, _light("Lightning storm", _solid(STORM, bri=40), 10.0), cues)
    return ["light profile Lightning storm (10 s loop, 4 flashes)"]


async def _chase(hub: Hub) -> list[str]:
    """One board after the other: checks targeting, order and per-board latency by eye."""
    macs = sorted(hub.boards.adopted_macs(), key=lambda m: hub.boards.boards[m].name or m)
    if not macs:
        return ["Board chase skipped: no adopted boards"]
    step = 0.5
    cues = [_flash(i * step, step, {"all": False, "macs": [m]}) for i, m in enumerate(macs)]
    length = max(2.0, len(macs) * step + 0.5)
    await _upsert_profile(hub, _light("Board chase", _solid(RED, bri=20), length), cues)
    return [f"light profile Board chase ({len(macs)} boards, {length:g} s loop)"]


async def _rooms(hub: Hub) -> list[str]:
    """Two test rooms from the adopted boards, each with its own colour override."""
    macs = sorted(hub.boards.adopted_macs(), key=lambda m: hub.boards.boards[m].name or m)
    if len(macs) < 2:
        return ["Rooms skipped: needs at least 2 adopted boards"]
    half = (len(macs) + 1) // 2
    a = await _upsert_group(hub, "Room A", "#ffbe00", macs[:half])
    b = await _upsert_group(hub, "Room B", "#c80000", macs[half:])
    body = ProfileIn.model_validate(
        {
            "name": "Rooms yellow/red",
            "type": "light",
            "base_state": _solid(WHITE, bri=150),
            "overrides": {f"group:{a}": {"colors": [YELLOW]}, f"group:{b}": {"colors": [RED]}},
        }
    )
    await _upsert_profile(hub, body)
    return [f"rooms Room A ({half}) and Room B ({len(macs) - half}), profile Rooms yellow/red"]


async def _scene_and_moment(hub: Hub) -> list[str]:
    """A shuffle scene over the looks and a moment button; needs the look presets."""
    from spookhub.api import routes_moments as rm

    notes = await _looks(hub)
    notes += await _lightning(hub)
    ids = [_find(Profile, PREFIX + n) for n in ("Warm white", "Rainbow", "Blood pulse")]
    idle = _find(Profile, PREFIX + "Candle flicker")
    scene = ProfileIn.model_validate(
        {
            "name": "Shuffle scene",
            "type": "scene",
            "scene": {
                "items": [{"profile_id": i} for i in ids if i is not None],
                "order": "shuffle",
                "idle_profile_id": idle,
                "light_hold_s": 8,
            },
        }
    )
    await _upsert_profile(hub, scene)
    storm = _find(Profile, PREFIX + "Lightning storm")
    moment = MomentIn.model_validate(
        {
            "name": PREFIX + "Lightning strike",
            "icon": "⚡",
            "color": "#4f6bff",
            "variants": [{"light_profile_id": storm}],
        }
    )
    mid = _find(Moment, moment.name)
    if mid is None:
        rm.create(moment, hub)
    else:
        rm.update(mid, moment, hub)
    return [*notes, "scene Shuffle scene (trigger = next look, 8 s), moment Lightning strike"]


def _settings(**values: Any) -> Callable[[Hub], Awaitable[list[str]]]:
    async def run(hub: Hub) -> list[str]:
        hub.settings.update(**values)
        return [", ".join(f"{k} = {v:g}" for k, v in values.items())]

    return run


PRESETS: list[Preset] = [
    Preset("looks", "Basic looks", "Warm white, rainbow, candle, blood pulse.", "show", _looks),
    Preset(
        "lightning",
        "Lightning storm",
        "Dark blue with white flashes (idle loop).",
        "show",
        _lightning,
    ),  # fmt: skip
    Preset(
        "chase", "Board chase", "Lights the adopted boards one after the other.", "show", _chase
    ),  # fmt: skip
    Preset(
        "rooms",
        "Two rooms",
        "Splits adopted boards into Room A / B, yellow vs red.",
        "show",
        _rooms,
    ),  # fmt: skip
    Preset(
        "scene",
        "Scene + moment",
        "Shuffle scene over the looks and a Lightning moment.",
        "show",
        _scene_and_moment,
    ),  # fmt: skip
    Preset(
        "rehearsal",
        "Rehearsal caps",
        "Brightness cap 50 %, volume cap 60, volume 40.",
        "settings",
        _settings(brightness_cap=0.5, volume_cap=60, master_volume=40),
    ),  # fmt: skip
    Preset(
        "quiet",
        "Silent check",
        "Volume 0, brightness 30 %: lights only, no sound.",
        "settings",
        _settings(master_volume=0, master_brightness=0.3),
    ),  # fmt: skip
    Preset(
        "full",
        "Show night",
        "Caps off: brightness and volume to 100 %.",
        "settings",
        _settings(brightness_cap=1.0, volume_cap=100, master_brightness=1.0, master_volume=100),
    ),  # fmt: skip
]
BY_ID = {p.id: p for p in PRESETS}


async def remove_all(hub: Hub) -> list[str]:
    """Delete every `[Test]` moment, profile (scenes first) and room."""
    from spookhub.api import routes_groups as rg
    from spookhub.api import routes_moments as rm
    from spookhub.api import routes_profiles as rp

    like = col(Profile.name).startswith(PREFIX)
    with db.session() as s:
        moments = [m.id for m in s.exec(select(Moment).where(col(Moment.name).startswith(PREFIX)))]
        profiles = sorted(
            ((p.type != ProfileType.scene, p.id) for p in s.exec(select(Profile).where(like))),
        )
        groups = [
            g.id for g in s.exec(select(BoardGroup).where(col(BoardGroup.name).startswith(PREFIX)))
        ]
    for mid in moments:
        rm.delete(mid or 0, hub)
    for _, pid in profiles:
        await rp.delete(pid or 0, hub)
    for gid in groups:
        await rg.delete(gid or 0, hub)
    return [f"removed {len(moments)} moments, {len(profiles)} profiles, {len(groups)} rooms"]
