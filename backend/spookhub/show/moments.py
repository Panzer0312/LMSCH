"""Moments: one-shot buttons that play a variant once, then restore what ran before.

Run: snapshot the engine (active profile, phase, idle clip position, ambient position), run the
variant's parts together (video profile once with its cues as the main play, sound on fx, light
profile base on its boards, ambience keep/mute/replace), end when the longest part ends, then
restore the snapshot without re-activating the profile: the idle clip resumes where it was, the
idle track and ambient carry on.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from spookhub import db
from spookhub.models import Media, Moment, MomentIn, MomentVariant, PlaybackMode, ProfileType
from spookhub.show.picker import Picker
from spookhub.show.resolve import Timeline
from spookhub.tasks import spawn

if TYPE_CHECKING:
    from spookhub.show.engine import ActiveProfile, Phase, ShowEngine

log = logging.getLogger(__name__)

OWNER = "moment"
AMBIENT_FADE_MS = 400
LIGHT_ONLY_S = 5.0
PROGRESS_S = 0.25
HISTORY = 20


class MomentRefused(RuntimeError):
    """Blackout, setup, cooldown or busy: the press does nothing (HTTP 409)."""


@dataclass
class Snapshot:
    active: ActiveProfile | None
    timeline: Timeline | None
    phase: Phase
    idle_pos: float | None
    ambient_pos: float | None
    # Boards any moment part drove since the snapshot (interrupts accumulate).
    touched: set[str] = field(default_factory=set)
    # An interrupted moment changed the picture / stopped the idle track.
    used_player: bool = False
    used_sched: bool = False


@dataclass
class Run:
    moment_id: int
    variant: int
    v: MomentVariant
    test: bool
    cooldown_s: float
    snap: Snapshot
    total: float = 0.0
    started: float = field(default_factory=time.monotonic)
    owns_main: bool = False  # the variant's video is the engine's main play
    used_player: bool = False  # main picture changed: reload the idle clip on restore
    used_sched: bool = False  # idle track stopped: restart it on restore
    sound_s: float = 0.0
    light_s: float = LIGHT_ONLY_S
    video_done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class MomentRunner:
    def __init__(self, engine: ShowEngine) -> None:
        self.e = engine
        self.run: Run | None = None
        self.queued: tuple[int, int | None] | None = None
        self.cooldown_until: dict[int, float] = {}
        self.rng: random.Random | None = None  # injectable for tests
        self._pickers: dict[int, Picker] = {}
        self._history: dict[int, list[int]] = {}
        self._last_state: dict[str, Any] | None = None

    # ----- state -----

    @property
    def running(self) -> bool:
        return self.run is not None

    def owns_main(self) -> bool:
        return self.run is not None and self.run.owns_main

    def home(self) -> ActiveProfile | None:
        """The profile the running moment returns to."""
        return self.run.snap.active if self.run else None

    def state(self) -> dict[str, Any]:
        now = time.monotonic()
        r = self.run
        progress = 0.0
        if r is not None and r.total > 0:
            progress = min(1.0, (now - r.started) / r.total)
        return {
            "playing_id": r.moment_id if r else None,
            "variant": r.variant if r else None,
            "progress": round(progress, 3),
            "cooldown": {
                str(mid): round(until - now, 1)
                for mid, until in self.cooldown_until.items()
                if until > now
            },
        }

    def publish(self, force: bool = False) -> None:
        st = self.state()
        if force or st != self._last_state:
            self._last_state = st
            self.e.bus.publish("moment", st)

    # ----- press -----

    def blocker(self) -> str | None:
        e = self.e
        if e.blackout:
            return "blackout active"
        if e._setup_task is not None:
            return "setup test running"
        if e.test_pattern:
            return "test pattern or video setup active"
        if e.audition is not None:
            return "live audition running"
        return None

    def busy(self) -> bool:
        e = self.e
        return self.run is not None or e.playing_main or e._scene_hold is not None

    async def play(self, moment_id: int, variant: int | None = None) -> dict[str, Any]:
        """Press a button. `variant` = test that variant: ignores and sets no cooldown."""
        m = _load(moment_id)
        if variant is not None and not 0 <= variant < len(m.variants):
            raise ValueError(f"variant {variant} out of range")
        if why := self.blocker():
            raise MomentRefused(why)
        if variant is None:
            left = self.cooldown_until.get(moment_id, 0.0) - time.monotonic()
            if left > 0:
                raise MomentRefused(f"cooldown ({math.ceil(left)} s left)")
        e = self.e
        async with e._lock:
            if why := self.blocker():
                raise MomentRefused(why)
            snap: Snapshot | None = None
            if self.busy():
                if m.busy == "ignore":
                    raise MomentRefused("something is playing")
                if m.busy == "queue":
                    if self.queued is not None:
                        raise MomentRefused("a moment is already queued")
                    self.queued = (moment_id, variant)
                    e.bus.event("info", f"Moment '{m.name}' queued", kind="moment")
                    return self.state()
                snap = await self._interrupt()
            idx = variant if variant is not None else self._pick(moment_id, m)
            await self._start(moment_id, m, idx, variant is not None, snap)
        e._publish()
        self.publish(force=True)
        return self.state()

    def _pick(self, moment_id: int, m: MomentIn) -> int:
        p = self._pickers.get(moment_id)
        if p is None or p.order != m.order:
            p = self._pickers[moment_id] = Picker(m.order, self.rng)
        hist = self._history.setdefault(moment_id, [])
        idx = p.next([(i, v.weight) for i, v in enumerate(m.variants)], hist)
        assert idx is not None  # validated: at least one variant
        hist.append(idx)
        del hist[:-HISTORY]
        return idx

    async def _interrupt(self) -> Snapshot | None:
        """Busy policy 'interrupt'. Caller holds the lock. A running moment hands its snapshot
        over, so the new one restores what ran before both."""
        e = self.e
        r = self.run
        if r is not None:
            self.run = None
            if r.task is not None:
                r.task.cancel()
            await e._stop_scheduler()
            e.playing_main = False
            spawn(_quiet(self._fx_stop(0)))
            e.active, e.timeline = r.snap.active, r.snap.timeline
            r.snap.used_player |= r.used_player
            r.snap.used_sched |= r.used_sched
            return r.snap
        if e.playing_main:
            await e._end_show("interrupted by a moment")
        elif e._scene_hold is not None:
            e._scene_cancel_hold()
            e._scene_restore()
            e.phase = _phase().idle
        return None

    # ----- run -----

    def _snapshot(self) -> Snapshot:
        e = self.e
        a = e.active
        pos = None
        if a is not None and e.phase == _phase().idle and a.idle_path:
            pos = e.player.position()
        amb = e.ambient
        amb_pos = None
        if amb is not None and e._ambient_on is not None:
            amb_pos = getattr(amb, "position", lambda: None)()
        return Snapshot(a, e.timeline, e.phase, pos, amb_pos)

    async def _start(
        self, moment_id: int, m: MomentIn, idx: int, test: bool, snap: Snapshot | None
    ) -> None:
        """Caller holds the lock and has checked blocker / busy / cooldown."""
        from spookhub.show.engine import load_active

        e = self.e
        v = m.variants[idx]
        snap = snap or self._snapshot()
        vp = load_active(v.video_profile_id) if v.video_profile_id else None
        if vp is not None and (vp.type != ProfileType.video or not vp.media_path):
            vp = None
        lp = load_active(v.light_profile_id) if v.light_profile_id else None
        if lp is not None and lp.type != ProfileType.light:
            lp = None
        sound = _sound(v)
        if vp is None and lp is None and sound is None:
            self._restore_engine(snap)
            raise MomentRefused("variant has nothing playable (missing profile or media)")

        r = Run(moment_id, idx, v, test, m.cooldown_s, snap)
        r.used_player, r.used_sched = snap.used_player, snap.used_sched
        r.sound_s = sound[1] if sound else 0.0
        self.run = r
        if e._sched is not None:
            # Only the idle track can run here (busy/blocker excluded show, audition, setup).
            await e._stop_scheduler()
            r.used_sched = True
        adopted = e.boards.adopted_macs()
        vp_macs: set[str] = set()
        if vp is not None:
            vp.mode = PlaybackMode.once  # a moment plays once, whatever the profile's mode
            # Fade away from the home idle clip like a trigger; a failed start falls back to it.
            home = snap.active
            idle_home = home is not None and snap.phase == _phase().idle and home.idle_path
            vp.idle_path = home.idle_path if idle_home and home else None
            if home is not None:
                vp.fade_ms = home.fade_ms
            if sound is not None:
                vp.sounds = []  # the moment's own sound owns the fx player
            vp_macs = set(vp.macs(adopted))
            e.active = vp
            e.timeline = Timeline(vp.cues, adopted)
            e._apply_base(critical=True)
            snap.touched |= vp_macs
            r.used_player = r.used_sched = True
            r.owns_main = bool(await e._play_main(0.0)) and e.playing_main
            if not r.owns_main:
                e.playing_main = False
        if lp is not None:
            r.light_s = lp.idle_length_s or LIGHT_ONLY_S
            for mac in lp.macs(adopted):
                if mac not in vp_macs:  # the video's cues win on shared boards
                    e.boards.set_show_state(mac, lp.base_for(mac), critical=True)
                    snap.touched.add(mac)
            if vp is None:
                # Light look alone: its base plus one pass of its idle track.
                if sound is not None:
                    lp.idle_sounds = []
                e.active = lp
                e.timeline = Timeline(lp.cues, adopted)
                e.phase = _phase().lights
                await e._start_idle_track()
                r.used_sched = True
        if vp is not None and not r.owns_main and lp is None and sound is None:
            await self._restore(r)
            raise MomentRefused("video could not start (see event log)")
        video_s = (vp.media_duration or 0.0) if r.owns_main and vp else 0.0
        r.total = max(video_s, r.sound_s) or r.light_s
        if sound is not None and v.sound is not None:
            await _quiet(
                self._fx_play(
                    sound[0], volume=v.sound.volume, fade_in_ms=v.sound.fade_in_ms, owner=OWNER
                )
            )
        if v.ambience != "keep" and e.ambient is not None:
            spawn(_quiet(self._ambience_start(r)))
        e.bus.event("info", f"Moment '{m.name}' (variant {idx + 1})", kind="moment")
        r.task = asyncio.create_task(self._wait(r))

    async def _wait(self, r: Run) -> None:
        waits: list[Awaitable[Any]] = []
        if r.owns_main:
            waits.append(r.video_done.wait())
        if r.sound_s > 0:
            waits.append(asyncio.sleep(r.sound_s))
        if not waits:
            waits.append(asyncio.sleep(r.light_s))
        done = asyncio.ensure_future(asyncio.gather(*waits))
        try:
            while not done.done():
                await asyncio.wait({done}, timeout=PROGRESS_S)
                if self.run is r:
                    self.publish()
        except asyncio.CancelledError:
            done.cancel()
            raise
        async with self.e._lock:
            if self.run is not r:
                return
            await self._restore(r)
        self.e._publish()
        self.publish(force=True)
        self.kick()

    def main_ended(self) -> None:
        """Engine `_end_show` while the moment's video was the main play (eof, stall, error).
        Caller holds the lock and stopped the scheduler; the run's waiter restores."""
        r = self.run
        if r is None:
            return
        self.e.playing_main = False
        r.owns_main = False
        r.video_done.set()

    async def stop(self) -> dict[str, Any]:
        """End the running moment now and restore; drops the queue."""
        self.queued = None
        async with self.e._lock:
            r = self.run
            if r is not None:
                await self._restore(r, stopped=True)
        self.e._publish()
        self.publish(force=True)
        return self.state()

    async def _restore(self, r: Run, stopped: bool = False) -> None:
        """Back to the snapshot without re-activating it. Caller holds the lock."""
        e = self.e
        if self.run is r:
            self.run = None
        if r.task is not None and r.task is not asyncio.current_task():
            r.task.cancel()
        if r.v.sound is not None:
            spawn(_quiet(self._fx_stop(r.v.sound.fade_out_ms if stopped else 0)))
        if r.used_sched or e.playing_main:
            await e._stop_scheduler()
        e.playing_main = False
        snap = r.snap
        self._restore_engine(snap)
        home = snap.active
        self._restore_boards(snap, critical=True)
        if r.used_player and not e.blackout:
            # Same transition as idle -> main, backwards; mpv off (tests, headless) has no picture.
            fade = home.fade_ms if home is not None and e.player.cfg.enabled else 0
            await e._fade_out(fade)
            if home is not None and snap.phase == _phase().idle and home.idle_path:
                await e._safe(e.player.load(home.idle_path, start=snap.idle_pos or 0.0, loop=True))
            else:
                await e._safe(e.player.black())
            if e.player.fade < 1.0:
                e._fade_task = asyncio.create_task(e._fade_in(fade))
        if r.used_sched:
            await e._start_idle_track()
        await _quiet(self._ambience_restore(r))
        if not r.test and r.cooldown_s > 0:
            self.cooldown_until[r.moment_id] = time.monotonic() + r.cooldown_s
            # The client counts down itself: one more message when the cooldown is over.
            asyncio.get_running_loop().call_later(r.cooldown_s + 0.05, self.publish)
        e.bus.event("info", "Moment stopped" if stopped else "Moment ended", kind="moment")

    def _restore_engine(self, snap: Snapshot) -> None:
        e = self.e
        e.active, e.timeline = snap.active, snap.timeline
        if not e.blackout and not e.test_pattern:
            e.phase = snap.phase

    def _restore_boards(self, snap: Snapshot, critical: bool) -> None:
        if not snap.touched:
            return
        e = self.e
        adopted = e.boards.adopted_macs()
        home = snap.active
        home_macs = set(home.macs(adopted)) if home else set()
        for mac in snap.touched - home_macs:
            e.boards.set_show_state(mac, None, critical=critical)
        if home is not None:
            for mac in home_macs:
                e.boards.set_show_state(mac, home.base_for(mac), critical=critical)

    def abort(self) -> None:
        """Blackout / activate / deactivate / test pattern: drop the moment. Boards go back to
        the home base; picture and ambient belong to the caller."""
        self.queued = None
        r = self.run
        if r is None:
            return
        self.run = None
        if r.task is not None and r.task is not asyncio.current_task():
            r.task.cancel()
        e = self.e
        spawn(_quiet(self._fx_stop(0)))
        if r.owns_main:
            e.playing_main = False
        e.active, e.timeline = r.snap.active, r.snap.timeline
        self._restore_boards(r.snap, critical=False)
        amb, cur = e.ambient, e._ambient_on
        if amb is not None:
            if r.v.ambience == "replace":
                spawn(_quiet(amb.stop(0, owner=OWNER)))
            elif r.v.ambience == "mute" and cur is not None and amb.current == cur.path:
                # Same loop stays on after re-activation (no restart): undo the mute.
                amb.ramp(e._ambient_level(cur), AMBIENT_FADE_MS)
        self.publish(force=True)

    def kick(self) -> None:
        """Something ended: start the queued moment when nothing else plays."""
        if self.queued is not None and not self.busy():
            spawn(self._dequeue())

    async def _dequeue(self) -> None:
        q = self.queued
        if q is None or self.busy():
            return
        self.queued = None
        try:
            await self.play(*q)
        except (MomentRefused, KeyError, ValueError) as exc:
            self.e.bus.event("warn", f"Queued moment skipped: {exc}", kind="moment")

    # ----- players -----

    async def _fx_play(self, path: str, **kw: Any) -> None:
        if self.e.fx is not None:
            await self.e.fx.play(path, **kw)

    async def _fx_stop(self, fade_ms: int) -> None:
        if self.e.fx is not None:
            await self.e.fx.stop(fade_ms, owner=OWNER)

    async def _ambience_start(self, r: Run) -> None:
        amb = self.e.ambient
        assert amb is not None
        if amb.current:
            fade = amb.ramp(0.0, AMBIENT_FADE_MS)
            if asyncio.isfuture(fade):
                await fade  # replace: the new loop starts once the old one is silent
        if r.v.ambience == "replace" and r.v.ambience_media_id is not None:
            path = _media_path(r.v.ambience_media_id)
            if path and self.run is r:
                await amb.play(path, fade_in_ms=AMBIENT_FADE_MS, loop=True, owner=OWNER)

    async def _ambience_restore(self, r: Run) -> None:
        """Ambient back to the active profile's loop at its normal (unducked) level."""
        e = self.e
        amb = e.ambient
        if amb is None or e.blackout:
            return
        e._ducked = e.playing_main
        cur = e._ambient_on
        if cur is None:
            if r.v.ambience == "replace":
                await amb.stop(AMBIENT_FADE_MS, owner=OWNER)
            return
        level = e._ambient_level(cur)
        if amb.current == cur.path:
            amb.ramp(level, AMBIENT_FADE_MS)
            return
        await amb.play(
            cur.path,
            start=r.snap.ambient_pos or 0.0,
            volume=level,
            fade_in_ms=AMBIENT_FADE_MS,
            loop=True,
            owner=_ambient_owner(),
        )


# ----- helpers -----


def _phase() -> type[Phase]:
    from spookhub.show.engine import Phase

    return Phase


def _ambient_owner() -> str:
    from spookhub.show.engine import AMBIENT_OWNER

    return AMBIENT_OWNER


def _load(moment_id: int) -> MomentIn:
    with db.session() as s:
        row = s.get(Moment, moment_id)
        if row is None:
            raise KeyError(moment_id)
        return MomentIn.model_validate(row.model_dump())


def _media_path(media_id: int) -> str | None:
    with db.session() as s:
        m = s.get(Media, media_id)
    return m.path if m and Path(m.path).exists() else None


def _sound(v: MomentVariant) -> tuple[str, float] | None:
    if v.sound is None:
        return None
    with db.session() as s:
        m = s.get(Media, v.sound.media_id)
    if m is None or not Path(m.path).exists():
        return None
    return m.path, max(0.0, m.duration_s)


async def _quiet(coro: Awaitable[Any]) -> None:
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        log.warning("moment player command failed: %s", exc)
