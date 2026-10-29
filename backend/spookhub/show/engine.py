"""Show engine: active profile, cue scheduler driven by mpv's time-pos, triggers, blackout."""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlmodel import select

from spookhub import db
from spookhub.boards.registry import BoardManager
from spookhub.events import EventBus
from spookhub.models import (
    BoardSelection,
    Cue,
    CueTarget,
    LightState,
    Media,
    PlaybackMode,
    Profile,
    ProfileType,
)
from spookhub.player.process import MpvPlayer
from spookhub.settings_store import SettingsStore
from spookhub.show.calibration import CLIP_S, calibration_cues
from spookhub.show.resolve import CueSpec, Timeline
from spookhub.tasks import spawn

log = logging.getLogger(__name__)

SEEK_JUMP_S = 0.5
AUDITION_TIMEOUT_S = 3.0


@dataclass
class AuditionClock:
    """Playhead reported by the browser preview; interpolated between reports."""

    profile_id: int
    t: float
    playing: bool
    recv: float
    deadline: float

    def position(self) -> tuple[float | None, bool]:
        if not self.playing:
            return self.t, True
        return self.t + min(time.monotonic() - self.recv, AUDITION_TIMEOUT_S), False


POSITION_PUBLISH_S = 0.25


class Phase(enum.StrEnum):
    none = "none"  # no active profile
    lights = "lights"  # light profile active
    idle = "idle"  # video profile, waiting (idle video or black)
    playing = "playing"
    paused = "paused"
    blackout = "blackout"
    test_pattern = "test_pattern"


@dataclass
class ActiveProfile:
    id: int
    name: str
    type: ProfileType
    selection: BoardSelection
    base: LightState
    overrides: dict[str, LightState]
    mode: PlaybackMode
    cooldown_s: float
    media_path: str | None
    media_duration: float | None
    idle_path: str | None
    cues: list[CueSpec] = field(default_factory=list)

    def macs(self, adopted: list[str]) -> list[str]:
        # "All boards" is resolved at use time so boards adopted later join automatically.
        if self.selection.all:
            return list(adopted)
        return [m for m in self.selection.macs if m in adopted]

    def base_for(self, mac: str) -> LightState:
        return self.base.merged(self.overrides.get(mac))


def load_active(profile_id: int) -> ActiveProfile | None:
    with db.session() as s:
        p = s.get(Profile, profile_id)
        if p is None:
            return None
        media = s.get(Media, p.media_id) if p.media_id else None
        idle = s.get(Media, p.idle_media_id) if p.idle_media_id else None
        cues = s.exec(select(Cue).where(Cue.profile_id == profile_id)).all()
        specs = []
        for c in cues:
            tgt = CueTarget.model_validate(c.target or {})
            specs.append(
                CueSpec(
                    id=c.id or 0,
                    start=c.start_s,
                    duration=c.duration_s,
                    targets=None if tgt.all else frozenset(tgt.macs),
                    state=LightState.model_validate(c.state or {}),
                    transition_ms=c.transition_ms,
                )
            )
        return ActiveProfile(
            id=profile_id,
            name=p.name,
            type=ProfileType(p.type),
            selection=BoardSelection.model_validate(p.selection or {}),
            base=LightState.model_validate(p.base_state or {}),
            overrides={k: LightState.model_validate(v) for k, v in (p.overrides or {}).items()},
            mode=PlaybackMode(p.playback_mode),
            cooldown_s=p.cooldown_s,
            media_path=media.path if media and Path(media.path).exists() else None,
            media_duration=media.duration_s if media else None,
            idle_path=idle.path if idle and Path(idle.path).exists() else None,
            cues=specs,
        )


class SetupBusy(RuntimeError):
    """A setup action and a show/test would collide."""


class SetupUnavailable(RuntimeError):
    """Player cannot take the setup action (mpv down or disabled)."""


class ShowEngine:
    def __init__(
        self,
        boards: BoardManager,
        player: MpvPlayer,
        bus: EventBus,
        settings: SettingsStore,
        tick_s: float = 0.01,
    ) -> None:
        self.boards = boards
        self.player = player
        self.bus = bus
        self.settings = settings
        self.tick_s = tick_s
        self.active: ActiveProfile | None = None
        self.timeline: Timeline | None = None
        self.phase = Phase.none
        self.blackout = False
        self.test_pattern = False
        self.cooldown_until = 0.0
        self.playing_main = False
        self.last_trigger: dict[str, Any] | None = None
        self._setup_task: asyncio.Task[None] | None = None
        self._setup_cleanup: Callable[[], None] | None = None
        self.calibrating = False
        self._calib_off = asyncio.Event()
        self._last_cue: dict[str, int | None] = {}
        # What the scheduler is currently driving: main show or browser audition.
        self._sched_prof: ActiveProfile | None = None
        self._sched_tl: Timeline | None = None
        self._clock: Callable[[], tuple[float | None, bool]] = self._player_clock
        self.audition: AuditionClock | None = None
        self._last_t: float | None = None
        self._sched: asyncio.Task[None] | None = None
        self._pub: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        player.on_change = self._on_player_change

    # ----- lifecycle -----

    async def start(self) -> None:
        self._pub = asyncio.create_task(self._publish_loop())
        pid = self.settings.value.active_profile_id
        if pid is not None:
            try:
                await self.activate(pid)
            except Exception:  # noqa: BLE001
                log.exception("could not restore active profile %s", pid)

    async def stop(self) -> None:
        for t in (self._sched, self._pub):
            if t:
                t.cancel()

    # ----- state for UI -----

    def snapshot(self) -> dict[str, Any]:
        a = self.active
        return {
            "phase": self.phase.value,
            "blackout": self.blackout,
            "test_pattern": self.test_pattern,
            "profile": None
            if a is None
            else {"id": a.id, "name": a.name, "type": a.type.value, "mode": a.mode.value},
            "position": self.player.position() if self.playing_main else None,
            "duration": self.player.duration or (a.media_duration if a else None),
            "cooldown_remaining": max(0.0, self.cooldown_until - time.monotonic()),
            "audition": None
            if self.audition is None
            else {"profile_id": self.audition.profile_id, "playing": self.audition.playing},
            "player": self.player.state(),
            "last_trigger": self.last_trigger,
            "setup_busy": self._setup_task is not None,
            "calibrating": self.calibrating,
        }

    def _publish(self) -> None:
        self.bus.publish("show", self.snapshot())

    async def _publish_loop(self) -> None:
        while True:
            await asyncio.sleep(POSITION_PUBLISH_S)
            if self.playing_main:
                self._publish()

    # ----- profile activation -----

    async def activate(self, profile_id: int, autostart: bool = True) -> None:
        await self._cancel_setup()
        async with self._lock:
            await self._cancel_setup()  # a setup may have started between the two
            prof = load_active(profile_id)
            if prof is None:
                raise KeyError(profile_id)
            await self._stop_scheduler()
            self.audition = None
            self.test_pattern = False
            self.active = prof
            self.timeline = Timeline(prof.cues, self.boards.adopted_macs())
            if self.settings.value.active_profile_id != profile_id:
                self.settings.update(active_profile_id=profile_id)
            self.boards.clear_show_states()
            self._apply_base(critical=False)
            self.bus.event("info", f"Profile '{prof.name}' activated")
            if self.blackout:
                self.phase = Phase.blackout
            elif prof.type == ProfileType.light:
                self.phase = Phase.lights
                await self._safe(self.player.black())
            elif prof.mode == PlaybackMode.idle_trigger or not autostart:
                await self._go_idle()
            else:
                await self._play_main(0.0)
            self._publish()

    async def reload_if_active(self, profile_id: int) -> None:
        """Profile edited: refresh cues/base live without interrupting a running video."""
        if not self.active or self.active.id != profile_id:
            return
        prof = load_active(profile_id)
        if prof is None:
            await self.deactivate()
            return
        self.active = prof
        self.timeline = Timeline(prof.cues, self.boards.adopted_macs())
        if self.playing_main:
            self._sched_prof, self._sched_tl = prof, self.timeline
        self._last_cue.clear()  # forces re-apply on next tick
        if not self.playing_main and self.audition is None:
            self._apply_base(critical=False)
        self._publish()

    async def deactivate(self) -> None:
        await self._cancel_setup()
        async with self._lock:
            await self._cancel_setup()
            await self._stop_scheduler()
            self.active = None
            self.timeline = None
            self.phase = Phase.blackout if self.blackout else Phase.none
            self.settings.update(active_profile_id=None)
            await self._safe(self.player.black())
            self._publish()

    def boards_changed(self) -> None:
        """Adoption/removal: 'All boards' selections pick up new boards immediately."""
        if not self.active:
            return
        self.timeline = Timeline(self.active.cues, self.boards.adopted_macs())
        if self.playing_main:
            self._sched_prof, self._sched_tl = self.active, self.timeline
        self._last_cue.clear()
        if not self.playing_main and self.audition is None:
            self._apply_base(critical=False)

    async def next_profile(self) -> None:
        with db.session() as s:
            ids = [p.id for p in s.exec(select(Profile).order_by(Profile.sort, Profile.id))]
        if not ids:
            return
        cur = self.active.id if self.active else None
        nxt = ids[(ids.index(cur) + 1) % len(ids)] if cur in ids else ids[0]
        await self.activate(nxt)  # type: ignore[arg-type]

    def _apply_base(self, critical: bool) -> None:
        a = self.active
        if not a:
            return
        for mac in a.macs(self.boards.adopted_macs()):
            self.boards.set_show_state(mac, a.base_for(mac), critical)

    # ----- playback -----

    async def trigger(self, source: str = "button") -> tuple[bool, str]:
        accepted, message = False, "trigger failed"
        try:
            accepted, message = await self._trigger(source)
        finally:
            # Recorded even when refused or crashed: lets the setup page show a sensor arrives.
            self.last_trigger = {
                "ts": time.time(),
                "source": source,
                "accepted": accepted,
                "message": message,
            }
            self.bus.publish("trigger_seen", self.last_trigger)
        return accepted, message

    async def _trigger(self, source: str) -> tuple[bool, str]:
        a = self.active
        if self._setup_task is not None:
            return False, "setup test running"
        if self.blackout:
            return False, "blackout active"
        if self.test_pattern:
            return False, "test pattern showing"
        if not a or a.type != ProfileType.video:
            return False, "no video profile active"
        if a.mode != PlaybackMode.idle_trigger:
            return False, "profile is not in idle + trigger mode"
        if self.playing_main:
            return False, "already playing"
        remaining = self.cooldown_until - time.monotonic()
        if remaining > 0:
            return False, f"cooldown ({remaining:.0f} s left)"
        async with self._lock:
            if self._setup_task is not None:
                return False, "setup test running"
            started = await self._play_main(0.0)
        self._publish()
        if not started:
            return False, "video could not start (see event log)"
        self.bus.event("info", f"Triggered ({source})")
        return True, "triggered"

    async def play(self, from_s: float = 0.0) -> None:
        a = self.active
        if self._setup_task is not None:
            raise SetupBusy("setup test running")
        if self.blackout or not a or a.type != ProfileType.video:
            raise RuntimeError("no video profile active")
        async with self._lock:
            if self._setup_task is not None:
                raise SetupBusy("setup test running")
            started = await self._play_main(from_s)
        self._publish()
        if not started:
            raise RuntimeError("video could not start (see event log)")

    async def stop_show(self) -> None:
        async with self._lock:
            if self.playing_main:
                await self._end_show("stopped")
        self._publish()

    async def seek(self, t: float) -> None:
        if self.playing_main:
            await self._safe(self.player.seek(t))

    async def pause(self, paused: bool) -> None:
        if self.playing_main:
            await self._safe(self.player.set_pause(paused))

    async def _play_main(self, from_s: float) -> bool:
        a = self.active
        assert a
        if not a.media_path:
            self.bus.event("error", f"Profile '{a.name}' has no playable video")
            await self._go_idle()
            return False
        await self._stop_scheduler()
        if self.audition is not None:
            self.audition = None
            self._apply_base(critical=False)
        loop = a.mode == PlaybackMode.loop
        ok = await self._safe(self.player.load(a.media_path, start=from_s, loop=loop))
        if not ok:
            self.bus.event("error", "Video player not ready")
            await self._go_idle()
            return False
        self.playing_main = True
        self.phase = Phase.playing
        assert self.timeline
        self._start_scheduler(a, self.timeline, self._player_clock)
        return True

    async def _go_idle(self) -> None:
        self.playing_main = False
        a = self.active
        self.phase = Phase.idle
        if a and a.idle_path:
            await self._safe(self.player.load(a.idle_path, loop=True))
        else:
            await self._safe(self.player.black())

    async def _end_show(self, reason: str) -> None:
        await self._stop_scheduler()
        self.playing_main = False
        a = self.active
        if a:
            self.cooldown_until = time.monotonic() + a.cooldown_s
        self._apply_base(critical=True)
        self.bus.event("info", f"Show {reason}, boards back to base")
        await self._go_idle()

    async def _stop_scheduler(self) -> None:
        if self._sched and self._sched is not asyncio.current_task():
            self._sched.cancel()
            try:
                await self._sched
            except asyncio.CancelledError:
                pass
        self._sched = None

    def _on_player_change(self, prop: str) -> None:
        if not self.playing_main:
            return
        if prop == "eof-reached" and self.player.eof:
            spawn(self._finish("ended"))
        elif prop == "restart":
            # mpv crashed mid-show: return to a defined state rather than guess.
            spawn(self._finish("interrupted (player restarted)"))
        elif prop == "pause":
            self.phase = Phase.paused if self.player.paused else Phase.playing
            self._publish()

    async def _finish(self, reason: str) -> None:
        async with self._lock:
            if self.playing_main:
                await self._end_show(reason)
        self._publish()

    # ----- scheduler -----

    def _player_clock(self) -> tuple[float | None, bool]:
        return self.player.position(), self.player.paused

    def _start_scheduler(
        self,
        prof: ActiveProfile,
        tl: Timeline,
        clock: Callable[[], tuple[float | None, bool]],
    ) -> None:
        self._sched_prof, self._sched_tl, self._clock = prof, tl, clock
        # Boards already show base; a missing entry (after seek/edit) forces a re-send.
        self._last_cue = {m: None for m in prof.macs(self.boards.adopted_macs())}
        self._last_t = None
        self._sched = asyncio.create_task(self._scheduler())

    async def _scheduler(self) -> None:
        """Fire board states from the interpolated mpv playhead.

        Comparing the resolved cue per board against what we last sent handles cue starts,
        duration ends, loop wrap-around and seeks with one mechanism.
        """
        while True:
            await asyncio.sleep(self.tick_s)
            if self.audition is not None and time.monotonic() > self.audition.deadline:
                # Browser tab went away mid-audition: give the boards back.
                spawn(self.audition_stop())
                return
            t, paused = self._clock()
            if t is None or self._sched_tl is None or self._sched_prof is None:
                continue
            seek = self._last_t is not None and (
                t < self._last_t - 0.25 or t > self._last_t + SEEK_JUMP_S
            )
            self._last_t = t
            if seek:
                self._last_cue.clear()
            if paused and not seek:
                continue
            self._tick(t)

    def _tick(self, t: float) -> None:
        a = self._sched_prof
        tl = self._sched_tl
        assert a and tl
        offset = self.settings.value.sync_offset_ms
        for mac in a.macs(self.boards.adopted_macs()):
            lead = (self.boards.latency_comp_ms(mac) + offset) / 1000.0
            cue_id, state = tl.state_at(mac, t + lead, a.base_for(mac))
            if mac in self._last_cue and self._last_cue[mac] == cue_id:
                continue
            self._last_cue[mac] = cue_id
            self.boards.set_show_state(mac, state, critical=True)

    # ----- audition & play from here -----

    async def audition_update(self, profile_id: int, t: float, playing: bool) -> tuple[bool, str]:
        """Browser preview playhead → fire that profile's cues on the real boards."""
        if self.blackout:
            return False, "blackout active"
        if self._setup_task is not None:
            return False, "setup test running"
        if self.playing_main:
            return False, "show is playing on the projector"
        now = time.monotonic()
        aud = self.audition
        if aud is not None and aud.profile_id == profile_id:
            aud.t, aud.playing, aud.recv = t, playing, now
            aud.deadline = now + AUDITION_TIMEOUT_S
            return True, "ok"
        async with self._lock:
            if self._setup_task is not None:
                return False, "setup test running"
            prof = load_active(profile_id)
            if prof is None:
                return False, "profile not found"
            await self._stop_scheduler()
            self.audition = AuditionClock(profile_id, t, playing, now, now + AUDITION_TIMEOUT_S)
            tl = Timeline(prof.cues, self.boards.adopted_macs())
            for mac in prof.macs(self.boards.adopted_macs()):
                self.boards.set_show_state(mac, prof.base_for(mac), critical=True)
            self._start_scheduler(prof, tl, self.audition.position)
            self._last_cue.clear()  # apply state at the current position immediately
        self.bus.event("info", f"Live audition started ({prof.name})")
        self._publish()
        return True, "started"

    async def audition_stop(self) -> None:
        if self.audition is None:
            return
        async with self._lock:
            prof = self._sched_prof
            await self._stop_scheduler()
            self.audition = None
            # Boards go back to the active profile's base; boards outside it get the
            # auditioned profile's base rather than a frozen cue.
            adopted = self.boards.adopted_macs()
            active = set(self.active.macs(adopted)) if self.active else set()
            if prof:
                for mac in prof.macs(adopted):
                    if mac not in active:
                        self.boards.set_show_state(mac, prof.base_for(mac), critical=False)
            if self.playing_main:
                self._last_cue.clear()
            else:
                self._apply_base(critical=False)
        self.bus.event("info", "Live audition stopped")
        self._publish()

    async def play_profile_from(self, profile_id: int, t: float) -> None:
        if self._setup_task is not None:
            raise SetupBusy("setup test running")
        if self.blackout:
            raise RuntimeError("blackout active")
        await self.audition_stop()
        if not self.active or self.active.id != profile_id:
            await self.activate(profile_id, autostart=False)
        await self.play(t)

    # ----- test pattern -----

    async def set_test_pattern(self, on: bool, image: str | None = None) -> None:
        """Projector alignment grid. Stops any show; lights stay as they are."""
        if on:
            assert image
            async with self._lock:
                if self._setup_task is not None:
                    raise SetupBusy("setup test running")
                await self._stop_scheduler()
                if self.playing_main:
                    self.playing_main = False
                    self._apply_base(critical=False)
                self.audition = None
                self.test_pattern = True
                self.phase = Phase.test_pattern
                await self._safe(self.player.load(image, loop=True))
            self.bus.event("info", "Test pattern on")
        elif self.test_pattern:
            self.test_pattern = False
            self.bus.event("info", "Test pattern off")
            if self.blackout:
                self.phase = Phase.blackout
                await self._safe(self.player.black())
            elif self.active:
                await self.activate(self.active.id, autostart=False)
            else:
                self.phase = Phase.none
                await self._safe(self.player.black())
        self._publish()

    # ----- setup activities (test sound now; test-all / calibration later) -----

    def setup_blocker(self) -> str | None:
        """Why a setup action may not start now, or None."""
        if self._setup_task is not None:
            return "setup test running"
        if self.playing_main:
            return "a show is playing"
        if self.test_pattern:
            return "test pattern showing"
        if self.audition is not None:
            return "live audition running"
        if self.blackout:
            return "blackout active"
        return None

    async def begin_setup(
        self,
        start: Callable[[], Awaitable[bool]],
        wait: Callable[[], Awaitable[None]],
        cleanup: Callable[[], None] | None = None,
    ) -> None:
        """Run a setup action: `start` under the lock (False = player unavailable), then `wait`
        outside it, so blackout and other controls never queue behind a slow test.
        `cleanup` (idempotent) runs when the action ends or is cancelled, even if the task was
        cancelled before its first step, where a try/finally inside `wait` would never run."""
        async with self._lock:
            why = self.setup_blocker()
            if why:
                raise SetupBusy(why)
            if not await start():
                raise SetupUnavailable("player not available")
            self._setup_cleanup = cleanup
            self._setup_task = asyncio.create_task(self._run_setup(wait))
        self._publish()

    async def _run_setup(self, wait: Callable[[], Awaitable[None]]) -> None:
        me = asyncio.current_task()
        try:
            await wait()
            # Cancellation (blackout/activate) skips the restore: the canceller owns the next state.
            async with self._lock:
                if self._setup_task is me:
                    await self._restore_idle()
        except Exception:  # noqa: BLE001
            log.exception("setup action failed")
            # A failed wait must not strand the player/boards on the test picture.
            try:
                async with self._lock:
                    if self._setup_task is me:
                        await self._restore_idle()
            except Exception:  # noqa: BLE001
                log.exception("restore after failed setup action failed")
        finally:
            # Never leave setup_busy stuck, whatever failed above.
            if self._setup_task is me:
                self._setup_task = None
                self._run_cleanup()
                self._publish()

    async def _cancel_setup(self) -> None:
        t = self._setup_task
        if t is None:
            return
        self._setup_task = None
        t.cancel()
        self._run_cleanup()
        # wait() instead of awaiting t: a failed task must not raise into blackout, and our own
        # cancellation must still propagate.
        await asyncio.wait({t})
        if not t.cancelled() and t.exception() is not None:
            log.warning("setup task ended with error: %s", t.exception())

    def _run_cleanup(self) -> None:
        fn, self._setup_cleanup = self._setup_cleanup, None
        if fn is not None:
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("setup cleanup failed")

    async def _restore_idle(self) -> None:
        """Back to the idle picture of the active profile. Caller holds the lock."""
        # Something newer took over while the test ran; do not clobber it.
        if self.playing_main or self.test_pattern or self.audition is not None or self.blackout:
            return
        if self.active and load_active(self.active.id) is not None:
            if self.active.type == ProfileType.light:
                self.phase = Phase.lights
            else:
                await self._go_idle()
                return
        else:
            # Profile deleted while the test ran.
            self.active = None
            self.timeline = None
            self.phase = Phase.none
        await self._safe(self.player.black())

    async def play_test_sound(self, path: str, max_wait_s: float = 6.0) -> None:
        async def start() -> bool:
            return self.player.cfg.enabled and await self._safe(self.player.load(path))

        async def wait() -> None:
            await self._wait_played(path, max_wait_s)

        await self.begin_setup(start, wait)

    async def _wait_played(self, path: str, max_wait_s: float) -> None:
        # Path match first: a stale eof from the previous file must not end the test.
        try:
            async with asyncio.timeout(max_wait_s):
                while not (self.player.path == path and self.player.eof):  # noqa: ASYNC110
                    await asyncio.sleep(0.05)
        except TimeoutError:
            pass

    async def run_test_all(
        self, sound: str, pattern: str, flash_s: float = 2.0, pattern_s: float = 2.0
    ) -> None:
        """Boards white, speaker sweep, projector grid; then idle is restored by the setup runner.

        Player steps are skipped when mpv is off or fails: the board flash is still useful."""

        async def start() -> bool:
            self.boards.hold_identify()
            return True

        async def wait() -> None:
            await asyncio.sleep(flash_s)
            self.boards.release_identify()
            if not self.player.cfg.enabled:
                return
            if await self._safe(self.player.load(sound)):
                await self._wait_played(sound, 6.0)
            if await self._safe(self.player.load(pattern, loop=True)):
                await asyncio.sleep(pattern_s)

        # Cleanup also covers cancel: a leftover override would flash white after blackout ends.
        await self.begin_setup(start, wait, self.boards.release_identify)

    # ----- sync calibration -----

    async def set_calibration(self, on: bool, clip: str | None = None) -> None:
        """Looping flash/beep clip driven through the normal scheduler, so global and per-board
        offsets apply exactly as in a show. Runs as a setup action until turned off."""
        if not on:
            self._calib_off.set()
            return
        if self.calibrating:
            return
        assert clip
        self._calib_off = asyncio.Event()

        async def start() -> bool:
            if not (
                self.player.cfg.enabled and await self._safe(self.player.load(clip, loop=True))
            ):
                return False
            adopted = self.boards.adopted_macs()
            # Base off so every flash is visible; ids are unique so each fires once per loop.
            prof = ActiveProfile(
                id=-1,
                name="Calibration",
                type=ProfileType.video,
                selection=BoardSelection(all=True),
                base=LightState(on=False),
                overrides={},
                mode=PlaybackMode.loop,
                cooldown_s=0.0,
                media_path=clip,
                media_duration=float(CLIP_S),
                idle_path=None,
                cues=calibration_cues(),
            )
            self.calibrating = True
            self._start_scheduler(prof, Timeline(prof.cues, adopted), self._player_clock)
            for mac in adopted:
                self.boards.set_show_state(mac, prof.base, critical=False)
            return True

        async def wait() -> None:
            await self._calib_off.wait()
            await self._end_calibration()

        await self.begin_setup(start, wait, self._calibration_cleanup)

    def _calibration_cleanup(self) -> None:
        # Sync so it also runs on cancel (blackout/activate), where wait() never resumes.
        self.calibrating = False
        if self._sched and self._sched is not asyncio.current_task():
            self._sched.cancel()
            self._sched = None
        self._sched_prof = self._sched_tl = None

    async def _end_calibration(self) -> None:
        await self._stop_scheduler()
        self._calibration_cleanup()
        for mac in self.boards.adopted_macs():
            self.boards.set_show_state(mac, None, critical=False)
        self._apply_base(critical=False)

    # ----- blackout / master -----

    async def set_blackout(self, on: bool) -> None:
        if on == self.blackout:
            return
        self.blackout = on
        self.boards.set_blackout(on)
        if on:
            # Safety control: never waits for a setup test.
            await self._cancel_setup()
            async with self._lock:
                await self._cancel_setup()
                await self._stop_scheduler()
                self.playing_main = False
                self.audition = None
                await self._safe(self.player.black())
            self.phase = Phase.blackout
            self.bus.event("warn", "BLACKOUT engaged")
        else:
            self.bus.event("info", "Blackout released")
            if self.active:
                await self.activate(self.active.id)
            else:
                self.phase = Phase.none
        self._publish()

    async def _safe(self, coro: Any) -> bool:
        try:
            await coro
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("player command failed: %s", e)
            return False
