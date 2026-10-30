"""Show engine: active profile, cue scheduler driven by mpv's time-pos, triggers, blackout."""

from __future__ import annotations

import asyncio
import enum
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from spookhub import db
from spookhub.boards.registry import BoardManager
from spookhub.events import EventBus
from spookhub.models import (
    AmbientSpec,
    BoardSelection,
    Cue,
    CueTarget,
    DisplayGeometry,
    LightState,
    Media,
    PlaybackMode,
    Profile,
    ProfileType,
    RuntimeSettings,
    SceneSpec,
)
from spookhub.player.process import MpvPlayer
from spookhub.settings_store import SettingsStore
from spookhub.show import display
from spookhub.show.calibration import CLIP_S, calibration_cues
from spookhub.show.moments import MomentRunner
from spookhub.show.resolve import CueSpec, Timeline
from spookhub.show.scene import IDLE_STATE, SceneRunner
from spookhub.show.sound_lane import FxPlayer, SoundCue, SoundLane, load_sound_cues
from spookhub.tasks import spawn

log = logging.getLogger(__name__)

SEEK_JUMP_S = 0.5
AUDITION_TIMEOUT_S = 3.0
STALL_S = 2.0  # frozen time-pos this long while playing = hung decoder/output
STALL_POLL_S = 0.25
STALL_RESTART_TIMEOUT_S = 20.0
# vo=null accepts the shader options (tests, headless) without rendering anything.
WARP_VOS = ("gpu-next", "null")
FADE_STEP_S = 0.02  # idle -> main fade: one brightness/volume IPC step per ~20 ms
AMBIENT_OWNER = "ambient"
AMBIENT_FADE_MS = 1000  # ambient start/stop/volume change outside a duck


@dataclass(frozen=True)
class AmbientSound:
    path: str
    volume: float
    duck_to: float


class AmbientPlayer(FxPlayer, Protocol):
    current: str | None

    def ramp(self, volume: float, ms: int, then_stop: bool = False) -> Any: ...


@dataclass
class AuditionClock:
    """Playhead reported by the browser preview; interpolated between reports."""

    profile_id: int
    t: float
    playing: bool
    recv: float
    deadline: float
    # MACs held at the profile base (muted in the editor).
    muted: frozenset[str] = frozenset()

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
    display_profile_id: int | None = None
    sounds: list[SoundCue] = field(default_factory=list)
    scene: SceneSpec | None = None
    # Idle track: loops with the idle clip, or every idle_length_s without one.
    idle_cues: list[CueSpec] = field(default_factory=list)
    idle_sounds: list[SoundCue] = field(default_factory=list)
    idle_length_s: float | None = None
    ambient: AmbientSound | None = None
    fade_ms: int = 400

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
        if p.type == ProfileType.scene:
            return _load_scene(p)
        media = s.get(Media, p.media_id) if p.media_id else None
        idle = s.get(Media, p.idle_media_id) if p.idle_media_id else None
        cues = s.exec(select(Cue).where(Cue.profile_id == profile_id)).all()
        main = [c for c in cues if c.track != "idle"]
        idle_rows = [c for c in cues if c.track == "idle"]
        specs: list[CueSpec] = []
        idle_specs: list[CueSpec] = []
        for c in cues:
            if c.kind == "sound":
                continue  # fx lane, see sounds below
            tgt = CueTarget.model_validate(c.target or {})
            (idle_specs if c.track == "idle" else specs).append(
                CueSpec(
                    id=c.id or 0,
                    start=c.start_s,
                    duration=c.duration_s,
                    targets=None if tgt.all else frozenset(tgt.macs),
                    state=LightState.model_validate(c.state or {}),
                    transition_ms=c.transition_ms,
                    segs=(
                        None
                        if tgt.segs is None
                        else {m: frozenset(ids) for m, ids in tgt.segs.items()}
                    ),
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
            display_profile_id=p.display_profile_id,
            sounds=load_sound_cues(s, main),
            idle_cues=idle_specs,
            idle_sounds=load_sound_cues(s, idle_rows),
            idle_length_s=p.idle_length_s if p.type == ProfileType.light else None,
            ambient=_load_ambient(s, p),
            fade_ms=p.fade_ms,
        )


def _load_ambient(s: Session, p: Profile) -> AmbientSound | None:
    if not p.ambient:
        return None
    try:
        spec = AmbientSpec.model_validate(p.ambient)
    except ValueError:
        log.warning("profile %s has an invalid ambient spec", p.id)
        return None
    m = s.get(Media, spec.media_id)
    if m is None or not Path(m.path).exists():
        return None
    return AmbientSound(m.path, spec.volume, spec.duck_to)


def _load_scene(p: Profile) -> ActiveProfile:
    """A scene's idle part: the idle profile's boards, picture and idle track under the scene's
    id. Ambient: the scene's own, else the idle profile's."""
    spec = SceneSpec.model_validate(p.scene or {})
    idle = load_active(spec.idle_profile_id) if spec.idle_profile_id else None
    if idle is not None and idle.type == ProfileType.scene:
        idle = None
    video = idle is not None and idle.type == ProfileType.video
    with db.session() as s:
        own_ambient = _load_ambient(s, p)
    return ActiveProfile(
        id=p.id or 0,
        name=p.name,
        type=ProfileType.scene,
        selection=idle.selection if idle else BoardSelection.model_validate(p.selection or {}),
        base=idle.base if idle else LightState.model_validate(p.base_state or {}),
        overrides=idle.overrides
        if idle
        else {k: LightState.model_validate(v) for k, v in (p.overrides or {}).items()},
        mode=PlaybackMode.idle_trigger,
        cooldown_s=p.cooldown_s,
        media_path=None,
        media_duration=None,
        # A video idle profile without an idle clip loops its main video as the idle picture.
        idle_path=(idle.idle_path or idle.media_path) if video and idle else None,
        display_profile_id=idle.display_profile_id if idle else p.display_profile_id,
        scene=spec,
        idle_cues=idle.idle_cues if idle else [],
        idle_sounds=idle.idle_sounds if idle else [],
        idle_length_s=idle.idle_length_s if idle else None,
        ambient=own_ambient or (idle.ambient if idle else None),
        fade_ms=p.fade_ms,
    )


SETUP_ACTIVE = "setup active — leave video setup first"


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
        # time.time() of the last accepted external trigger: the sensor-works signal.
        self.last_external_ts: float | None = None
        self._pattern_image: str | None = None
        self._calib_clip: str | None = None
        # One player recovery (stall or crash) per play; a second failure ends the show.
        self._recovered = False
        self._setup_task: asyncio.Task[None] | None = None
        self._setup_cleanup: Callable[[], None] | None = None
        self.calibrating = False
        self._calib_off = asyncio.Event()
        self._last_cue: dict[str, Any] = {}  # mac -> last sent cue key (None = base)
        # What the scheduler is currently driving: main show or browser audition.
        self._sched_prof: ActiveProfile | None = None
        self._sched_tl: Timeline | None = None
        self._clock: Callable[[], tuple[float | None, bool]] = self._player_clock
        self.audition: AuditionClock | None = None
        self._last_t: float | None = None
        self._sched: asyncio.Task[None] | None = None
        self._stall_task: asyncio.Task[None] | None = None
        self._stall_restarted = asyncio.Event()
        self._stall_pending = False
        # Stall clock floor: unpause/seek make time-pos legitimately stand still for a while.
        self._resumed_mono = 0.0
        self.stall_s = STALL_S
        self.stall_poll_s = STALL_POLL_S
        self.stall_restart_timeout_s = STALL_RESTART_TIMEOUT_S
        self._pub: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        # Video setup mode (/video): rides on the test-pattern state so every rule that keeps
        # shows away from a test pattern (trigger refusal, lock, blackout) applies unchanged.
        self.display_setup = False
        self.setup_source: dict[str, Any] | None = None
        self.live: DisplayGeometry | None = None
        # Media row behind the setup source; its display profile is re-read on every resolve
        # so assignment changes and profile deletes apply without re-entering setup.
        self._setup_media_id: int | None = None
        self._setup_size: tuple[int, ...] | None = None
        # What mpv shows outside setup, as resolved by the per-load hook.
        self._display_path: str | None = None
        self._shown = DisplayGeometry()
        self._shown_id: int | None = None
        self._shown_size: tuple[int, ...] | None = None
        # Source sizes of files that are not Media rows (generated patterns).
        self.known_sizes: dict[str, tuple[int, int]] = {}
        self._last_display: dict[str, Any] | None = None
        # Scene: runner + its idle part; self.active is the playing item while one runs.
        self.scene: SceneRunner | None = None
        self._scene_home: ActiveProfile | None = None
        self._scene_hold: asyncio.Task[None] | None = None
        self.scene_rng: random.Random | None = None  # injectable for tests
        # Audio-only effects player for sound cues (set by the hub; None = sound cues off).
        self.fx: FxPlayer | None = None
        self._sound = SoundLane()
        # Moments (/moments buttons): one-shot runs that restore the state afterwards.
        self.moments = MomentRunner(self)
        # Ambient loop player (set by the hub; None = ambient off) and what it loops now.
        self.ambient: AmbientPlayer | None = None
        self._ambient_on: AmbientSound | None = None
        self._ducked = False
        # Scheduler drives the idle track (not main/audition/calibration); mono = no idle clip.
        self._idle_track = False
        self._idle_mono = False
        self._fade_task: asyncio.Task[None] | None = None
        player.on_change = self._on_player_change
        player.geometry_for = self._geometry_for
        settings.listeners.append(self._on_settings)

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
        await self._cancel_setup()
        tasks = [t for t in (self._stall_task, self._sched, self._pub) if t]
        self._stall_task = self._sched = self._pub = None
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ----- state for UI -----

    def snapshot(self) -> dict[str, Any]:
        a = self._scene_home or (self.moments.home() if self.moments.running else self.active)
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
        self._publish_display()

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
            self.moments.abort()
            prof = load_active(profile_id)
            if prof is None:
                raise KeyError(profile_id)
            await self._stop_scheduler()
            self.audition = None
            self.test_pattern = False
            self._clear_display_setup()
            self.active = prof
            self._scene_setup(prof)
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
                await self._start_idle_track()
            elif prof.mode == PlaybackMode.idle_trigger or not autostart:
                await self._go_idle()
            else:
                await self._play_main(0.0)
            await self._sync_ambient()
            if self.scene is not None and self.scene.spec.chain and autostart:
                spawn(self._scene_chain())
            self._publish()

    async def reload_if_active(self, profile_id: int) -> None:
        """Profile edited: refresh cues/base live without interrupting a running video."""
        if self.scene is not None:
            # Item edits apply the next time the item plays.
            if self._scene_home and profile_id in (self.scene.profile_id, self._scene_home_idle()):
                await self._scene_reload()
            return
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
            if self.phase in (Phase.idle, Phase.lights):
                await self._start_idle_track()
        await self._sync_ambient()
        self.refresh_display()
        self._publish()

    async def deactivate(self) -> None:
        await self._cancel_setup()
        async with self._lock:
            await self._cancel_setup()
            self.moments.abort()
            await self._stop_scheduler()
            self._scene_setup(None)
            self.active = None
            self.timeline = None
            self.phase = Phase.blackout if self.blackout else Phase.none
            self.settings.update(active_profile_id=None)
            await self._safe(self.player.black())
            await self._stop_fx()
            await self._sync_ambient()
            self._publish()

    def boards_changed(self) -> None:
        """Adoption/removal: 'All boards' selections pick up new boards immediately."""
        if self.calibrating and self._sched_prof is not None:
            prof = self._sched_prof
            self._sched_tl = Timeline(prof.cues, self.boards.adopted_macs())
            for mac in prof.macs(self.boards.adopted_macs()):
                if mac not in self._last_cue:
                    self._last_cue[mac] = None
                    self.boards.set_show_state(mac, prof.base, critical=False)
            return
        if not self.active:
            return
        self.timeline = Timeline(self.active.cues, self.boards.adopted_macs())
        if self.playing_main:
            self._sched_prof, self._sched_tl = self.active, self.timeline
        self._last_cue.clear()
        if self._idle_track and self._sched_prof is not None:
            # The next tick re-sends base or idle cue to every board, new ones included.
            self._sched_tl = Timeline(self._sched_prof.idle_cues, self.boards.adopted_macs())
        elif not self.playing_main and self.audition is None:
            self._apply_base(critical=False)

    async def next_profile(self) -> None:
        with db.session() as s:
            ids = [p.id for p in s.exec(select(Profile).order_by(Profile.sort, Profile.id))]
        if not ids:
            return
        cur = self.home_id()
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
            if accepted and source == "external":
                self.last_external_ts = self.last_trigger["ts"]
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
        if self.moments.running:
            return False, "a moment is playing"
        if self.scene is not None:
            return await self._scene_trigger(source)
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
        self.bus.event("info", f"Triggered ({source})", kind="trigger", source=source)
        return True, "triggered"

    async def play(self, from_s: float = 0.0) -> None:
        a = self.active
        if self._setup_task is not None:
            raise SetupBusy("setup test running")
        if self.test_pattern:  # covers video setup, which rides on the test pattern
            raise SetupBusy(SETUP_ACTIVE)
        if self.moments.running:
            raise RuntimeError("a moment is playing")
        if self.blackout or not a or a.type != ProfileType.video:
            raise RuntimeError("no video profile active")
        async with self._lock:
            if self._setup_task is not None:
                raise SetupBusy("setup test running")
            if self.test_pattern:
                raise SetupBusy(SETUP_ACTIVE)
            started = await self._play_main(from_s)
        self._publish()
        if not started:
            raise RuntimeError("video could not start (see event log)")

    async def stop_show(self) -> None:
        if self.moments.running:
            await self.moments.stop()
        async with self._lock:
            if self.playing_main:
                await self._end_show("stopped")
        await self._stop_fx()
        self._publish()

    async def seek(self, t: float) -> None:
        if self.playing_main:
            self._resumed_mono = time.monotonic()  # hr-seek can hold time-pos for a while
            await self._safe(self.player.seek(t))
            self._resumed_mono = time.monotonic()

    async def pause(self, paused: bool) -> None:
        if self.playing_main:
            await self._safe(self.player.set_pause(paused))
            if self.fx is not None and self._sound.playing is not None:
                await self._safe(self.fx.set_pause(paused))

    async def _play_main(self, from_s: float) -> bool:
        a = self.active
        assert a
        if not a.media_path:
            self.bus.event("error", f"Profile '{a.name}' has no playable video")
            await self._go_idle()
            return False
        home = self._scene_home or a
        # Fade only away from a visible idle clip; mpv off (tests, headless) has no picture.
        fade = (
            home.fade_ms
            if self.phase == Phase.idle
            and home.idle_path is not None
            and self.player.path is not None
            and self.player.cfg.enabled
            else 0
        )
        await self._stop_scheduler()
        if self.audition is not None:
            self.audition = None
            self._apply_base(critical=False)
        self._duck_ambient(True, home.fade_ms)
        await self._fade_out(fade)
        if self.blackout:  # pressed during the fade; blackout takes over the player
            await self._reset_fade()
            return False
        loop = a.mode == PlaybackMode.loop
        ok = await self._safe(self.player.load(a.media_path, start=from_s, loop=loop))
        if not ok:
            self.bus.event("error", "Video player not ready")
            await self._go_idle()
            return False
        if self.player.fade < 1.0:
            self._fade_task = asyncio.create_task(self._fade_in(fade))
        self.playing_main = True
        self._recovered = False
        self.phase = Phase.playing
        assert self.timeline
        self._start_scheduler(a, self.timeline, self._player_clock)
        # Main show only: calibration, test sounds and idle loops never get stall recovery.
        self._resumed_mono = time.monotonic()
        if self.player.cfg.enabled:
            self._stall_task = asyncio.create_task(self._stall_watch(a))
        return True

    async def _go_idle(self) -> None:
        self.playing_main = False
        self._scene_restore()
        await self._reset_fade()
        a = self.active
        self.phase = Phase.idle
        self._duck_ambient(False, a.fade_ms if a else 0)
        if a and a.idle_path:
            await self._safe(self.player.load(a.idle_path, loop=True))
        else:
            await self._safe(self.player.black())
        await self._start_idle_track()

    async def _end_show(self, reason: str) -> None:
        await self._stop_scheduler()
        if self.moments.owns_main():
            self.moments.main_ended()  # the moment restores; no profile cooldown
            return
        self.playing_main = False
        self._scene_restore()  # cooldown and base below are the scene's
        a = self.active
        if a:
            self.cooldown_until = time.monotonic() + a.cooldown_s
        self._apply_base(critical=True)
        self.bus.event("info", f"Show {reason}, boards back to base")
        await self._go_idle()
        self.moments.kick()

    async def _stop_scheduler(self) -> None:
        if self._stall_task and self._stall_task is not asyncio.current_task():
            self._stall_task.cancel()
        self._stall_task = None
        self._stall_pending = False
        if self._sched and self._sched is not asyncio.current_task():
            self._sched.cancel()
            try:
                await self._sched
            except asyncio.CancelledError:
                pass
        self._sched = None
        self._idle_track = self._idle_mono = False
        self._sound.stop(self.fx)
        await self._reset_fade()

    async def _stop_fx(self) -> None:
        """Stop / blackout / deactivate: silence every effects sound, soundboard included."""
        self._sound.stop(None)
        if self.fx is not None:
            await self._safe(self.fx.stop())

    def _on_player_change(self, prop: str) -> None:
        if prop == "restart":
            if self._stall_pending:
                self._stall_restarted.set()  # our own stall restart
            elif self.playing_main:
                spawn(self._crash_resume())
            else:
                # A fresh mpv shows nothing: put back whatever the current phase wants.
                spawn(self._reapply_phase())
            return
        if not self.playing_main:
            return
        if prop == "eof-reached" and self.player.eof:
            spawn(self._finish("ended"))
        elif prop == "pause":
            if not self.player.paused:
                self._resumed_mono = time.monotonic()
            self.phase = Phase.paused if self.player.paused else Phase.playing
            self._publish()

    async def _finish(self, reason: str) -> None:
        async with self._lock:
            if self.playing_main:
                await self._end_show(reason)
        self._publish()
        if reason == "ended" and self.scene is not None and self.scene.spec.chain:
            spawn(self._scene_chain())

    async def _crash_resume(self) -> None:
        """mpv died mid-show and came back: resume once where it was, like a stall."""
        a = self.active
        if not self.playing_main or a is None:
            return
        if self._recovered:
            self.bus.event("error", "video player restarted again, ending show", kind="player")
            await self._finish("interrupted (player restarted)")
            return
        self._recovered = True
        at = self._last_t or 0.0
        self.bus.event("warn", "video player restarted, resuming", kind="player")
        if not await self._resume(a, at):
            await self._finish("interrupted (player did not recover)")

    async def _reapply_phase(self) -> None:
        """Player (re)started outside a show: restore the picture of the current phase."""
        async with self._lock:
            if self.playing_main:
                return
            if self.blackout:
                await self._safe(self.player.black())
            elif self.test_pattern:
                if self._pattern_image:
                    await self._safe(self.player.load(self._pattern_image, loop=True))
                else:  # video setup with an idle source that has no idle video
                    await self._safe(self.player.black())
            elif self.calibrating and self._calib_clip:
                if await self._safe(self.player.load(self._calib_clip, loop=True)):
                    self._last_cue = {m: None for m in self._last_cue}
                    self._last_t = None
            elif self._setup_task is not None:
                return  # short test (sound / test-all): its runner restores idle at the end
            elif self.active and (self.scene is not None or self.active.type == ProfileType.video):
                await self._go_idle()
            else:
                await self._safe(self.player.black())
        self._publish()

    # ----- stall watchdog -----

    def _stalled(self) -> bool:
        p = self.player
        if p.paused or p.eof or p.loading or p.seeking or p.buffering or p.time_pos is None:
            return False
        return time.monotonic() - max(p.pos_mono, self._resumed_mono) >= self.stall_s

    async def _stall_watch(self, a: ActiveProfile) -> None:
        """Restart a frozen mpv once and resume; a second failure in the same play ends the show."""
        seen: float | None = None
        while True:
            await asyncio.sleep(self.stall_poll_s)
            if not self.playing_main or not self._stalled():
                seen = None
                continue
            if seen != self.player.time_pos:  # confirm on a second poll at the same position
                seen = self.player.time_pos
                continue
            if self._recovered:
                self.bus.event("error", "video stalled again, ending show", kind="player")
                await self._finish("stalled")
                return
            self._recovered = True
            at = self.player.time_pos or 0.0
            self.bus.event("warn", "video stalled, restarting", kind="player")
            if not await self._restart_and_resume(a, at):
                await self._finish("stalled (player did not recover)")
                return

    async def _restart_and_resume(self, a: ActiveProfile, at: float) -> bool:
        self._stall_restarted.clear()
        self._stall_pending = True
        try:
            await self.player.restart()
            async with asyncio.timeout(self.stall_restart_timeout_s):
                await self._stall_restarted.wait()
        except TimeoutError:
            return False
        finally:
            self._stall_pending = False
        return await self._resume(a, at)

    async def _resume(self, a: ActiveProfile, at: float) -> bool:
        """Reload the show video at `at` on a freshly started player."""
        async with self._lock:
            if not self.playing_main or self.active is not a:
                return True  # show ended meanwhile; nothing to resume
            assert a.media_path
            loop = a.mode == PlaybackMode.loop
            ok = await self._safe(self.player.load(a.media_path, start=at, loop=loop))
            # Re-resolve the cue at the resume position for every board.
            self._last_cue = {m: None for m in self._last_cue}
            self._last_t = None
            self._resumed_mono = time.monotonic()
            return ok

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
                self._sound.resync()
            if paused and not seek:
                continue
            self._tick(t)

    def _tick(self, t: float) -> None:
        a = self._sched_prof
        tl = self._sched_tl
        assert a and tl
        if self._idle_track:
            if self._idle_mono:
                self._sound.tick(self.fx, a.idle_sounds, t)
            elif not self.player.paused:
                self._sound.tick(self.fx, a.idle_sounds, t - self.settings.value.audio_delay_s)
        elif (
            self.playing_main
            and self.audition is None
            and not self.calibrating
            and not self.player.paused  # a seek while paused re-syncs on resume
        ):
            # Main audio-delay shifts the soundtrack against time-pos; sounds follow it.
            self._sound.tick(self.fx, a.sounds, t - self.settings.value.audio_delay_s)
        offset = self.settings.value.sync_offset_ms
        for mac in a.macs(self.boards.adopted_macs()):
            lead = (self.boards.latency_comp_ms(mac) + offset) / 1000.0
            at = t + lead
            if self.calibrating:
                # The clip loops: a lead past the end (or before 0) must see the neighbouring
                # loop's flash, else the t=0 flash is lost for boards that fire early.
                at %= CLIP_S
            segs: dict[int, LightState] = {}
            key: Any
            if self.audition is not None and mac in self.audition.muted:
                key, state = -1, a.base_for(mac)  # sentinel: no real cue has id -1
            else:
                (key, seg_key), state, segs = tl.board_at(mac, at, a.base_for(mac))
                if seg_key:
                    # Segment cues change the payload too; plain cue ids keep None = base.
                    key = (key, seg_key)
            if mac in self._last_cue and self._last_cue[mac] == key:
                continue
            self._last_cue[mac] = key
            # One payload per board: segment states ride along in its seg list.
            self.boards.set_show_state(mac, state, critical=True, segs=segs)

    # ----- audition & play from here -----

    async def audition_update(
        self, profile_id: int, t: float, playing: bool, muted: list[str] | None = None
    ) -> tuple[bool, str]:
        """Browser preview playhead → fire that profile's cues on the real boards."""
        if self.blackout:
            return False, "blackout active"
        if self._setup_task is not None:
            return False, "setup test running"
        if self.playing_main:
            return False, "show is playing on the projector"
        if self.moments.running:
            return False, "a moment is playing"
        now = time.monotonic()
        aud = self.audition
        if aud is not None and aud.profile_id == profile_id:
            aud.t, aud.playing, aud.recv = t, playing, now
            aud.deadline = now + AUDITION_TIMEOUT_S
            new_muted = frozenset(muted or [])
            if new_muted != aud.muted:
                aud.muted = new_muted
                # Force a re-resolve; a paused scheduler would not tick on its own.
                self._last_cue.clear()
                if not playing:
                    self._tick(t)
            return True, "ok"
        async with self._lock:
            if self._setup_task is not None:
                return False, "setup test running"
            prof = load_active(profile_id)
            if prof is None:
                return False, "profile not found"
            await self._stop_scheduler()
            self.audition = AuditionClock(
                profile_id, t, playing, now, now + AUDITION_TIMEOUT_S, frozenset(muted or [])
            )
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
                if self.phase in (Phase.idle, Phase.lights):
                    await self._start_idle_track()
        self.bus.event("info", "Live audition stopped")
        self._publish()

    async def play_profile_from(self, profile_id: int, t: float) -> None:
        if self._setup_task is not None:
            raise SetupBusy("setup test running")
        # Checked before activate(), which would silently end the test pattern / video setup.
        if self.test_pattern:
            raise SetupBusy(SETUP_ACTIVE)
        if self.blackout:
            raise RuntimeError("blackout active")
        await self.audition_stop()
        if not self.active or self.active.id != profile_id:
            await self.activate(profile_id, autostart=False)
        await self.play(t)

    # ----- scenes -----

    def home_id(self) -> int | None:
        """The activated profile: the scene while one is active, even mid-item."""
        if self._scene_home is not None:
            return self._scene_home.id
        if self.moments.running:
            home = self.moments.home()
            return home.id if home else None
        return self.active.id if self.active else None

    def moment_state(self) -> dict[str, Any]:
        return self.moments.state()

    async def play_moment(self, moment_id: int, variant: int | None = None) -> dict[str, Any]:
        """Raises KeyError (unknown), ValueError (bad variant), MomentRefused (409)."""
        return await self.moments.play(moment_id, variant)

    async def stop_moment(self) -> dict[str, Any]:
        return await self.moments.stop()

    def scene_state(self) -> dict[str, Any]:
        return self.scene.state() if self.scene else dict(IDLE_STATE)

    def _publish_scene(self) -> None:
        self.bus.publish("scene", self.scene_state())

    def _scene_home_idle(self) -> int | None:
        h = self._scene_home
        return h.scene.idle_profile_id if h and h.scene else None

    def _scene_setup(self, prof: ActiveProfile | None) -> None:
        """Activation: keep the runner (history, pin) when the same scene comes back."""
        self._scene_cancel_hold()
        if prof is None or prof.scene is None:
            changed = self.scene is not None
            self.scene = self._scene_home = None
        else:
            changed = True
            if self.scene is not None and self.scene.profile_id == prof.id:
                self.scene.update(prof.scene)
            else:
                self.scene = SceneRunner(prof.id, prof.scene, self.scene_rng)
            self._scene_home = prof
        if changed:
            self._publish_scene()

    def _scene_cancel_hold(self) -> None:
        if self._scene_hold is not None and self._scene_hold is not asyncio.current_task():
            self._scene_hold.cancel()
        self._scene_hold = None

    def _scene_busy(self) -> bool:
        return self.playing_main or self._scene_hold is not None

    def _scene_restore(self) -> None:
        """Item over: back to the scene's idle part without re-activating it. Boards only;
        the caller owns the picture (an idle loop under a light item never stopped)."""
        home = self._scene_home
        item = self.active
        if home is None or item is home:
            return
        adopted = self.boards.adopted_macs()
        self.active = home
        self.timeline = Timeline(home.cues, adopted)
        home_macs = set(home.macs(adopted))
        if item is not None:
            for mac in item.macs(adopted):
                if mac not in home_macs:
                    self.boards.set_show_state(mac, None, critical=True)
        for mac in home_macs:
            self.boards.set_show_state(mac, home.base_for(mac), critical=True)

    async def _scene_reload(self) -> None:
        assert self.scene is not None
        prof = load_active(self.scene.profile_id)
        if prof is None:
            await self.deactivate()
            return
        if prof.scene is None:  # no longer a scene
            await self.activate(prof.id, autostart=False)
            return
        old = self._scene_home
        self.scene.update(prof.scene)
        self._scene_home = prof
        if self.active is old:
            self.active = prof
            self.timeline = Timeline(prof.cues, self.boards.adopted_macs())
            if self.audition is None and not self.blackout:
                self._apply_base(critical=False)
                if self.phase == Phase.idle and (old is None or old.idle_path != prof.idle_path):
                    await self._go_idle()
                elif self.phase == Phase.idle:
                    await self._start_idle_track()
        await self._sync_ambient()
        self.refresh_display()
        self._publish()
        self._publish_scene()

    async def _scene_trigger(self, source: str) -> tuple[bool, str]:
        if self._scene_busy():
            return False, "already playing"
        remaining = self.cooldown_until - time.monotonic()
        if remaining > 0:
            return False, f"cooldown ({remaining:.0f} s left)"
        async with self._lock:
            if self._setup_task is not None:
                return False, "setup test running"
            if self.scene is None or self._scene_busy():
                return False, "already playing" if self.scene else "no scene active"
            ok, msg = await self._scene_play_next()
        self._publish()
        if ok:
            self.bus.event("info", f"Triggered ({source})", kind="trigger", source=source)
        return ok, msg

    async def _scene_play_next(self) -> tuple[bool, str]:
        """Start the next item. Caller holds the lock."""
        assert self.scene is not None and self._scene_home is not None
        pid = self.scene.take()
        self._publish_scene()
        if pid is None:
            return False, "scene has no items"
        item = load_active(pid)
        if item is None or item.type == ProfileType.scene:
            return False, f"scene item {pid} not found"
        self.bus.event("info", f"Scene '{self._scene_home.name}': playing '{item.name}'")
        await self._stop_scheduler()  # idle track off: the item owns the boards now
        self.active = item
        self.timeline = Timeline(item.cues, self.boards.adopted_macs())
        # The scheduler assumes boards already show the base: send it before the video.
        self._apply_base(critical=True)
        if item.type == ProfileType.light:
            self.phase = Phase.lights
            self._scene_hold = asyncio.create_task(
                self._scene_light_hold(item, self.scene.spec.light_hold_s)
            )
            return True, "triggered"
        item.mode = PlaybackMode.once  # a scene item plays once, whatever its own mode
        if not await self._play_main(0.0):
            return False, "video could not start (see event log)"  # _go_idle restored idle
        return True, "triggered"

    async def _scene_light_hold(self, item: ActiveProfile, hold_s: float) -> None:
        await asyncio.sleep(hold_s)
        async with self._lock:
            if self._scene_hold is not asyncio.current_task():
                return
            self._scene_hold = None
            if self.active is not item or self.test_pattern or self.blackout:
                return
            self._scene_restore()
            if self.active is not None:
                self.cooldown_until = time.monotonic() + self.active.cooldown_s
            self.phase = Phase.idle
            await self._start_idle_track()
        self._publish()
        self.moments.kick()
        if self.scene is not None and self.scene.spec.chain:
            await self._scene_chain()

    async def _scene_chain(self) -> None:
        """Chain mode: the next item starts as soon as the last one ended."""
        async with self._lock:
            sc = self.scene
            if (
                sc is None
                or not sc.spec.chain
                or self._scene_busy()
                or self.blackout
                or self.test_pattern
                or self._setup_task is not None
                or self.audition is not None
            ):
                return
            ok, msg = await self._scene_play_next()
        if not ok:
            # No retry loop: a broken item would spin. The next trigger restarts the chain.
            self.bus.event("warn", f"Scene chain paused: {msg}")
        self._publish()

    async def scene_skip(self) -> dict[str, Any]:
        if self.scene is None:
            raise RuntimeError("no scene active")
        self.scene.skip()
        self._publish_scene()
        return self.scene_state()

    async def scene_pin(self, profile_id: int | None) -> dict[str, Any]:
        if self.scene is None:
            raise RuntimeError("no scene active")
        self.scene.pin(profile_id)
        self._publish_scene()
        return self.scene_state()

    # ----- idle track -----

    async def _start_idle_track(self) -> None:
        """(Re)start the active profile's idle cue track: timed off the idle clip's time-pos
        (wraps with its loop), or a monotonic loop of idle_length_s without a clip."""
        await self._stop_scheduler()
        a = self.active
        if (
            a is None
            or self.playing_main
            or self.blackout
            or self.test_pattern
            or self.calibrating
            or self.audition is not None
            or self._setup_task is not None
            or not (a.idle_cues or a.idle_sounds)
        ):
            return
        if a.idle_path is not None and self.phase == Phase.idle:
            clock, mono = self._player_clock, False
        elif a.idle_length_s:
            t0, length = time.monotonic(), a.idle_length_s

            def clock() -> tuple[float | None, bool]:
                return (time.monotonic() - t0) % length, False

            mono = True
        else:
            return
        self._start_scheduler(a, Timeline(a.idle_cues, self.boards.adopted_macs()), clock)
        self._idle_track, self._idle_mono = True, mono
        # Boards may still show an idle cue from before the restart: re-send everything.
        self._last_cue.clear()

    # ----- ambient -----

    def _ambient_level(self, amb: AmbientSound) -> float:
        return amb.volume * (amb.duck_to if self._ducked else 1.0)

    async def _sync_ambient(self) -> None:
        """Ambient loop follows the activated profile (scene included); blackout and
        deactivate stop it. An unchanged loop keeps playing (no restart on re-activate)."""
        amb = self.ambient
        if amb is None:
            return
        home = self._scene_home or self.active
        want = None if self.blackout or home is None else home.ambient
        cur = self._ambient_on
        self._ducked = self.playing_main
        self._ambient_on = want
        if want is None:
            if cur is not None:
                await self._safe(amb.stop(0 if self.blackout else 300, owner=AMBIENT_OWNER))
            return
        level = self._ambient_level(want)
        if cur is None or cur.path != want.path or amb.current != want.path:
            await self._safe(
                amb.play(
                    want.path,
                    volume=level,
                    fade_in_ms=AMBIENT_FADE_MS,
                    loop=True,
                    owner=AMBIENT_OWNER,
                )
            )
        elif cur != want:
            amb.ramp(level, AMBIENT_FADE_MS)

    def _duck_ambient(self, on: bool, ms: int) -> None:
        """Main video starts (on) / ends (off): ramp the ambient to duck_to*volume and back."""
        if on == self._ducked:
            return
        self._ducked = on
        amb, cur = self.ambient, self._ambient_on
        if amb is not None and cur is not None and amb.current == cur.path:
            amb.ramp(self._ambient_level(cur), ms)

    # ----- transition fade -----

    async def _fade_to(self, level: float, ms: int) -> None:
        start = self.player.fade
        steps = max(1, round(ms / 1000 / FADE_STEP_S))
        for i in range(1, steps + 1):
            await self._safe(self.player.set_fade(start + (level - start) * i / steps))
            if i < steps:
                await asyncio.sleep(FADE_STEP_S)

    async def _fade_out(self, ms: int) -> None:
        """Ramp the main player to black (brightness -> -100) and silence over `ms`.

        0 = hard cut: nothing is sent. The level stays at 0, so the next load starts black
        and silent; `_fade_in` (or `_reset_fade`) brings it back."""
        if ms > 0:
            await self._fade_to(0.0, ms)

    async def _fade_in(self, ms: int) -> None:
        """Ramp back to the display geometry's brightness and the master volume over `ms`;
        0 = at once (no-op when nothing is faded)."""
        if self.player.fade >= 1.0:
            return
        if ms > 0:
            await self._fade_to(1.0, ms)
        else:
            await self._safe(self.player.set_fade(1.0))

    async def _reset_fade(self) -> None:
        """Cancel a running fade-in and restore full level (anything that loads other media)."""
        t, self._fade_task = self._fade_task, None
        if t is not None and t is not asyncio.current_task():
            t.cancel()
        if self.player.fade < 1.0:
            await self._safe(self.player.set_fade(1.0))

    # ----- test pattern -----

    async def set_test_pattern(self, on: bool, image: str | None = None) -> None:
        """Projector alignment grid. Stops any show; lights stay as they are."""
        if on:
            assert image
            async with self._lock:
                if self._setup_task is not None:
                    raise SetupBusy("setup test running")
                if self.blackout:
                    raise SetupBusy("blackout active")
                self._pattern_image = image
                self.moments.abort()
                await self._stop_scheduler()
                if self.playing_main:
                    self.playing_main = False
                    self._apply_base(critical=False)
                self.audition = None
                self.test_pattern = True
                self.phase = Phase.test_pattern
                self._clear_display_setup()
                await self._safe(self.player.load(image, loop=True))
            self.bus.event("info", "Test pattern on")
        elif self.test_pattern:
            self.test_pattern = False
            was_setup = self.display_setup
            self._clear_display_setup()
            self.bus.event("info", "Video setup off" if was_setup else "Test pattern off")
            if self.blackout:
                self.phase = Phase.blackout
                await self._safe(self.player.black())
            elif self.active:
                await self.activate(self.home_id() or self.active.id, autostart=False)
            else:
                self.phase = Phase.none
                await self._safe(self.player.black())
        self._publish()

    # ----- display geometry & video setup -----

    def _on_settings(self, old: RuntimeSettings, new: RuntimeSettings) -> None:
        if old.display_profile_id != new.display_profile_id:
            self.refresh_display()
        elif old.mpv_vo != new.mpv_vo:
            self._publish_display()

    def warp_supported(self) -> bool:
        """The warp shader's runtime parameters need libplacebo (vo=gpu-next)."""
        if not self.player.cfg.enabled:
            return True
        vo = self.settings.value.mpv_vo or self.player.cfg.vo
        return vo in WARP_VOS

    def _resolve_for(self, media: display.MediaInfo | None) -> tuple[int | None, DisplayGeometry]:
        return display.resolve(
            self.active.display_profile_id if self.active else None,
            media.display_profile_id if media else None,
            self.settings.value.display_profile_id,
        )

    def _setup_resolved(self) -> tuple[int | None, DisplayGeometry]:
        """Media source: media > global, the show profile's override is skipped because that
        video is what is being framed. Idle/pattern: the show's full chain (profile first)."""
        found = display.media_by_id(self._setup_media_id)
        kind = (self.setup_source or {}).get("kind")
        profile_dp = self.active.display_profile_id if self.active and kind != "media" else None
        return display.resolve(
            profile_dp,
            found[1].display_profile_id if found else None,
            self.settings.value.display_profile_id,
        )

    def _geometry_for(self, path: str) -> tuple[DisplayGeometry, tuple[int, int] | None]:
        """Per-load hook of the player: geometry for whatever is about to play."""
        self._display_path = path
        if self.display_setup:
            _, g = self._setup_resolved()
            return self.live or g, self._setup_size
        media = display.media_by_path(path)
        self._shown_id, self._shown = self._resolve_for(media)
        self._shown_size = media.size if media else self.known_sizes.get(path)
        self._publish_display()
        return self._shown, self._shown_size

    def refresh_display(self) -> None:
        """Display profiles or their assignment changed: re-resolve what is on screen now."""
        try:
            if self.display_setup:
                _, g = self._setup_resolved()
                self.player.apply_geometry(self.live or g, self._setup_size)
            else:
                media = display.media_by_path(self._display_path)
                self._shown_id, self._shown = self._resolve_for(media)
                self.player.apply_geometry(self._shown, self._shown_size)
        except Exception:  # noqa: BLE001
            log.exception("display refresh failed")
        self._publish_display()

    def display_state(self) -> dict[str, Any]:
        """WS `display` payload; `geometry` is what mpv shows (live edits included)."""
        if self.display_setup:
            rid, g = self._setup_resolved()
            return {
                "setup": True,
                "source": self.setup_source,
                "live": self.live.model_dump() if self.live else None,
                "resolved_profile_id": rid,
                "geometry": (self.live or g).model_dump(),
                "warp_supported": self.warp_supported(),
            }
        return {
            "setup": False,
            "source": None,
            "live": None,
            "resolved_profile_id": self._shown_id,
            "geometry": self._shown.model_dump(),
            "warp_supported": self.warp_supported(),
        }

    def _publish_display(self) -> None:
        try:
            st = self.display_state()
        except Exception:  # noqa: BLE001
            log.exception("display state failed")
            return
        if st != self._last_display:
            self._last_display = st
            self.bus.publish("display", st)

    def _clear_display_setup(self) -> None:
        """Leave setup mode (caller restores the picture). Unsaved live edits are dropped."""
        if not self.display_setup:
            return
        self.display_setup = False
        self.setup_source = None
        self.live = None
        self._setup_media_id = None
        self._setup_size = None
        self.refresh_display()

    async def enter_display_setup(
        self,
        source: dict[str, Any],
        path: str | None,
        media_id: int | None = None,
        size: tuple[int, ...] | None = None,
    ) -> None:
        """Enter video setup or switch its source. Stops any show, like the test pattern.

        `path` None shows black (idle source without an idle video)."""
        async with self._lock:
            if self._setup_task is not None:
                raise SetupBusy("setup test running")
            if self.blackout:
                raise SetupBusy("blackout active")
            entering = not self.display_setup
            self.moments.abort()
            await self._stop_scheduler()
            if self.playing_main:
                self.playing_main = False
                self._apply_base(critical=False)
            self.audition = None
            self.test_pattern = True
            self.phase = Phase.test_pattern
            self.display_setup = True
            self.setup_source = source
            self._setup_media_id = media_id
            self._setup_size = size
            self._pattern_image = path
            if path:
                await self._safe(self.player.load(path, loop=True))
            else:
                await self._safe(self.player.black())
            # Also covers players that skip the load hook.
            _, g = self._setup_resolved()
            self.player.apply_geometry(self.live or g, size)
        if entering:
            self.bus.event("info", "Video setup on")
        self._publish()

    async def exit_display_setup(self) -> None:
        """Leaving /video always clears the projector, also a plain test pattern from elsewhere."""
        if self.test_pattern:
            await self.set_test_pattern(False)

    def set_live(self, g: DisplayGeometry) -> None:
        """Unsaved geometry straight to the projector (the player coalesces slider bursts)."""
        if not self.display_setup:
            raise SetupBusy("video setup is not active")
        self.live = g
        self.player.apply_geometry(g, self._setup_size)
        self._publish_display()

    async def setup_seek(self, t: float) -> None:
        src = self.setup_source
        if not self.display_setup or src is None or src.get("kind") not in ("media", "idle"):
            raise SetupBusy("no video source to scrub")
        if not self._pattern_image:
            raise SetupBusy("no video source to scrub")
        await self._safe(self.player.seek(t))

    # ----- setup activities (test sound now; test-all / calibration later) -----

    def player_blocker(self) -> str | None:
        """Why mpv output settings (restart the player) may not change now, or None."""
        if self.playing_main:
            return "a show is playing"
        if self._setup_task is not None:
            return "setup test running"
        return None

    async def end_setup_for_lock(self) -> None:
        """Show lock on: a forgotten calibration / test / test pattern would refuse every
        trigger all night, and the lock blocks the setup pages that could clear it."""
        had_setup = self._setup_task is not None
        await self._cancel_setup()  # outside the lock: a slow test must not delay this
        async with self._lock:
            had_setup = had_setup or self._setup_task is not None
            await self._cancel_setup()
            if had_setup:
                # Cancel skips the runner's restore; calibration cleanup left boards unset.
                if not (self.playing_main or self.audition is not None or self.blackout):
                    self._apply_base(critical=False)
                await self._restore_idle()
        if self.test_pattern:
            await self.set_test_pattern(False)
        self._publish()

    def fallback_blocker(self) -> str | None:
        """Why board fallback may not be saved now (it rewrites live boards), or None."""
        if self.calibrating:
            return "calibration running"
        return self.setup_blocker()

    def setup_blocker(self) -> str | None:
        """Why a setup action may not start now, or None."""
        if self._setup_task is not None:
            return "setup test running"
        if self.moments.running:
            return "a moment is playing"
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
            if self._idle_track:  # the setup action owns player and boards; restore restarts it
                await self._stop_scheduler()
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
        if self.phase == Phase.lights:
            await self._start_idle_track()

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
        self._calib_clip = clip

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
            me = asyncio.current_task()
            await self._calib_off.wait()
            async with self._lock:
                if self._setup_task is me:
                    await self._end_calibration()

        await self.begin_setup(start, wait, self._calibration_cleanup)

    def _calibration_cleanup(self) -> None:
        # Sync so it also runs on cancel (blackout/activate), where wait() never resumes.
        # Idempotent: the setup runner calls it again after a normal end.
        if not self.calibrating:
            return
        self.calibrating = False
        # Without this a cancel mid-flash leaves the calibration state (white) on the boards.
        for mac in self.boards.adopted_macs():
            self.boards.set_show_state(mac, None, critical=False)
        if self._sched and self._sched is not asyncio.current_task():
            self._sched.cancel()
            self._sched = None
        self._sched_prof = self._sched_tl = None

    async def _end_calibration(self) -> None:
        await self._stop_scheduler()
        self._calibration_cleanup()
        self._apply_base(critical=False)

    # ----- blackout / master -----

    async def set_blackout(self, on: bool) -> None:
        if on == self.blackout:
            return
        self.blackout = on
        self.boards.set_blackout(on)
        if on:
            await self._stop_fx()
            await self._sync_ambient()
            # Safety control: never waits for a setup test.
            await self._cancel_setup()
            async with self._lock:
                await self._cancel_setup()
                self.moments.abort()
                await self._stop_scheduler()
                self.playing_main = False
                self.audition = None
                self._scene_cancel_hold()
                if self.test_pattern:
                    self.test_pattern = False
                    self._clear_display_setup()
                    self.bus.event("info", "Test pattern off")
                await self._safe(self.player.black())
            self.phase = Phase.blackout
            self.bus.event("warn", "BLACKOUT engaged")
        else:
            self.bus.event("info", "Blackout released")
            if self.active:
                await self.activate(self.home_id() or self.active.id)
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
