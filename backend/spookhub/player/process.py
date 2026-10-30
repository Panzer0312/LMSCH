"""mpv process supervisor plus the high-level playback API used by the show engine."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from spookhub.config import MpvCfg
from spookhub.events import EventBus
from spookhub.models import DisplayGeometry
from spookhub.player.devices import parse_audio_devices
from spookhub.player.geometry import (
    DEFAULT_PROPS,
    DEFAULT_SHADER_OPTS,
    WARP_SHADER,
    SourceFrame,
    to_mpv,
)
from spookhub.player.ipc import MpvError, MpvIpc
from spookhub.tasks import spawn

MAX_EXTRAPOLATE_S = 0.5

log = logging.getLogger("spookhub.mpv")

OBSERVED = (
    "time-pos",
    "pause",
    "eof-reached",
    "path",
    "duration",
    "seeking",
    "paused-for-cache",
)
READY_TIMEOUT_S = 5.0
QUICK_CRASH_S = 5.0
MAX_RESTART_DELAY_S = 10.0
STOP_GRACE_S = 3.0
GEOMETRY_COALESCE_S = 0.03

# Hook the engine sets: geometry + source size (w, h; None = unknown) for a path about to load.
GeometryFor = Callable[[str], tuple[DisplayGeometry, tuple[int, ...] | None]]


class MpvPlayer:
    def __init__(self, cfg: MpvCfg, bus: EventBus) -> None:
        self.cfg = cfg
        self.bus = bus
        self._loading: bool = False
        self.time_pos: float | None = None
        self.paused: bool = False
        self.eof: bool = False
        self._seeking: bool = False
        self._buffering: bool = False
        self.duration: float | None = None
        self.path: str | None = None
        self.pos_mono: float = time.monotonic()
        self.on_change: Callable[[str], None] | None = None
        self.vo_override: str | None = None
        self.gpu_context_override: str | None = None
        self.drm_connector_override: str | None = None
        self.drm_mode_override: str | None = None
        self.audio_device_override: str | None = None
        # Last applied audio state; re-sent after every (re)start and load().
        self._volume: float | None = None
        self._audio_delay_s: float | None = None
        self._launched_audio: tuple[float | None, float | None] = (None, None)
        self.ready: asyncio.Event = asyncio.Event()
        self._ipc: MpvIpc | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping: bool = False
        self._restart_requested: bool = False
        # Wall-clock times of unexpected mpv exits (preflight flags a crash loop).
        self.restart_times: list[float] = []
        # Monotonic start of the latest (requested or crash) restart; preflight grace window.
        self.restart_began: float | None = None
        # Display geometry: target (what should show) vs. what this mpv instance has.
        self.geometry_for: GeometryFor | None = None
        self.geometry: DisplayGeometry = DisplayGeometry()
        self._geom_size: tuple[int, ...] | None = None
        self._sent_props: dict[str, Any] = dict(DEFAULT_PROPS)
        self._sent_shader: dict[str, str] = dict(DEFAULT_SHADER_OPTS)
        self._geom_flush: asyncio.TimerHandle | None = None
        self._geom_lock = asyncio.Lock()
        # Transition fade 0..1 on top of geometry brightness and volume (1 = untouched).
        self.fade: float = 1.0

    # ---- lifecycle ---------------------------------------------------------------------------

    @property
    def socket_path(self) -> Path:
        # sun_path is limited to 108 bytes; mpv fails silently beyond that.
        p = self.cfg.socket_path
        if len(str(p)) <= 100:
            return p
        return Path(tempfile.gettempdir()) / f"spookhub-{os.getuid()}-mpv.sock"

    def args(self) -> list[str]:
        cfg = self.cfg
        vo = self.vo_override or cfg.vo
        ctx = self.gpu_context_override or cfg.gpu_context
        out = [
            cfg.binary,
            "--idle=yes",
            "--keep-open=yes",
            "--force-window=yes",
            "--background-color=#000000",
            "--osd-level=0",
            "--osc=no",
            "--no-input-default-bindings",
            "--input-terminal=no",
            "--no-terminal",
            "--cursor-autohide=always",
            "--fullscreen",
            f"--vo={vo}",
        ]
        if ctx:
            out.append(f"--gpu-context={ctx}")
        out.append(f"--hwdec={cfg.hwdec}")
        if cfg.ao:
            out.append(f"--ao={cfg.ao}")
        if self.drm_connector_override:
            out.append(f"--drm-connector={self.drm_connector_override}")
        if self.drm_mode_override:
            out.append(f"--drm-mode={self.drm_mode_override}")
        if self.audio_device_override:
            out.append(f"--audio-device={self.audio_device_override}")
        # Via args so every (re)start comes up with the remembered audio state.
        if self._volume is not None:
            out.append(f"--volume={self._volume:g}")
        if self._audio_delay_s is not None:
            out.append(f"--audio-delay={self._audio_delay_s:g}")
        out.append(f"--input-ipc-server={self.socket_path}")
        out.append("--hr-seek=yes")
        # Always loaded so keystone/mask/flip are a runtime parameter change, never a restart.
        # Only vo=gpu-next understands //!PARAM; vo=gpu logs an error and plays unwarped.
        out.append(f"--glsl-shaders={WARP_SHADER}")
        out.extend(cfg.extra_args)
        return out

    async def start(self) -> None:
        if not self.cfg.enabled or self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._supervise(), name="mpv-supervisor")

    async def stop(self) -> None:
        self._stopping = True
        if not self.cfg.enabled:
            return
        ipc = self._ipc
        if ipc is not None and ipc.connected:
            with contextlib.suppress(MpvError):
                await ipc.command("quit", timeout=1.0)
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                async with asyncio.timeout(STOP_GRACE_S):
                    await proc.wait()
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def restart(self) -> None:
        if not self.cfg.enabled:
            return
        # Supervisor sees the exit and respawns with current args (picks up vo overrides).
        self.restart_began = time.monotonic()
        proc = self._proc
        if proc is not None and proc.returncode is None:
            # Only with a live process: a stale flag would hide the next real crash.
            self._restart_requested = True
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    async def _supervise(self) -> None:
        delay = self.cfg.restart_delay_s
        while not self._stopping:
            started = time.monotonic()
            rc = await self._run_once()
            self._reset_state()
            if self._stopping:
                break
            if self._restart_requested:
                self._restart_requested = False
                delay = self.cfg.restart_delay_s
                continue
            self.bus.event("error", f"mpv exited (code {rc}), restarting", kind="player")
            self.restart_times = [t for t in self.restart_times if time.time() - t < 86400]
            self.restart_times.append(time.time())
            self.restart_began = time.monotonic()
            if time.monotonic() - started >= QUICK_CRASH_S:
                delay = self.cfg.restart_delay_s
            await asyncio.sleep(delay)
            # Back off only while mpv keeps dying right after launch (e.g. no DRM master).
            delay = min(delay * 2, MAX_RESTART_DELAY_S)

    async def _run_once(self) -> int | None:
        sock = self.socket_path
        try:
            sock.parent.mkdir(parents=True, exist_ok=True)
            # A stale socket from a crashed mpv would make connect() hit a dead endpoint.
            sock.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("cannot prepare mpv socket %s: %s", sock, exc)
        args = self.args()
        self._launched_audio = (self._volume, self._audio_delay_s)
        log.info("starting mpv: %s", " ".join(args))
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            log.error("cannot launch mpv (%s): %s", self.cfg.binary, exc)
            return None
        self._proc = proc
        stderr_task = asyncio.create_task(self._drain_stderr(proc), name="mpv-stderr")
        ipc = MpvIpc()
        ipc.on_event = self._on_event
        self._ipc = ipc
        try:
            try:
                await ipc.connect(sock)
                for name in OBSERVED:
                    await ipc.observe(name)
                await self._sync_audio_after_launch(ipc)
                # A fresh mpv starts at defaults: re-send whatever geometry differs from them.
                self._sent_props = dict(DEFAULT_PROPS)
                self._sent_shader = dict(DEFAULT_SHADER_OPTS)
                await self._send_geometry(ipc)
            except MpvError as exc:
                log.warning("mpv ipc setup failed: %s", exc)
            else:
                self.ready.set()
                self.restart_began = None
                self._notify("restart")
                waiters = {
                    asyncio.create_task(proc.wait()),
                    asyncio.create_task(ipc.closed.wait()),
                }
                try:
                    await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for w in waiters:
                        w.cancel()
            # Without a working socket the process is uncontrollable, so never leave it running.
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            return proc.returncode
        finally:
            self.ready.clear()
            await ipc.close()
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            stderr_task.cancel()
            self._ipc = None

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        stream = proc.stderr
        if stream is None:
            return
        # Must keep reading: a full stderr pipe would block mpv mid-playback.
        while True:
            try:
                line = await stream.readline()
            except ValueError:
                # Over-long line: asyncio already discarded it, keep draining.
                continue
            except OSError:
                return
            if not line:
                return
            log.debug("%s", line.decode(errors="replace").rstrip())

    @property
    def loading(self) -> bool:
        return self._loading

    @property
    def seeking(self) -> bool:
        return self._seeking

    @property
    def buffering(self) -> bool:
        """mpv paused itself to refill its cache; time-pos legitimately stands still."""
        return self._buffering

    def _reset_state(self) -> None:
        self._seeking = self._buffering = False
        self.time_pos = None
        self.path = None
        self.eof = False
        self.pos_mono = time.monotonic()

    # ---- property tracking -------------------------------------------------------------------

    def _on_event(self, msg: dict[str, Any]) -> None:
        ev = msg.get("event")
        if ev == "start-file":
            # Old file's positions may still trickle in; ignore until the new one really plays.
            self._loading = True
            self.time_pos = None
            return
        if ev == "playback-restart":
            self._loading = False
            return
        if ev != "property-change":
            return
        name = msg.get("name")
        data = msg.get("data")
        match name:
            case "time-pos":
                if self._loading:
                    return
                self.time_pos = float(data) if isinstance(data, (int, float)) else None
                self.pos_mono = time.monotonic()
            case "pause":
                self.paused = bool(data)
            case "seeking":
                self._seeking = bool(data)
            case "paused-for-cache":
                self._buffering = bool(data)
            case "eof-reached":
                self.eof = bool(data)
            case "path":
                self.path = data if isinstance(data, str) else None
            case "duration":
                self.duration = float(data) if isinstance(data, (int, float)) else None
            case _:
                return
        self._notify(name)

    def _notify(self, name: str) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change(name)
        except Exception:
            log.exception("mpv on_change handler failed")

    def position(self) -> float | None:
        if self.time_pos is None:
            return None
        if self.paused or self.eof:
            return self.time_pos
        # Cap extrapolation: if mpv stalls, hold instead of running ahead of the picture.
        return self.time_pos + min(time.monotonic() - self.pos_mono, MAX_EXTRAPOLATE_S)

    def state(self) -> dict[str, Any]:
        return {
            "ready": self.ready.is_set(),
            "path": self.path,
            "time_pos": self.position(),
            "duration": self.duration,
            "paused": self.paused,
            "eof": self.eof,
        }

    # ---- high-level API ----------------------------------------------------------------------

    async def _ready_ipc(self) -> MpvIpc:
        try:
            async with asyncio.timeout(READY_TIMEOUT_S):
                await self.ready.wait()
        except TimeoutError as exc:
            raise MpvError("mpv not ready") from exc
        ipc = self._ipc
        if ipc is None or not ipc.connected:
            raise MpvError("mpv not ready")
        return ipc

    async def load(self, path: str, start: float = 0.0, loop: bool = False) -> None:
        # Hook first, even with mpv disabled: the engine tracks what is shown through it.
        self._run_geometry_hook(path)
        if not self.cfg.enabled:
            return
        ipc = await self._ready_ipc()
        # Before loadfile so the first frame already has the right framing.
        await self._send_geometry(ipc)
        # Per-file options (mpv >= 0.38 signature: url, flags, index, options) reset after the
        # file, so a loop restarts at 0 rather than at a global --start.
        # Re-assert audio before the file starts so its first samples use the right level.
        await self._reapply_audio(ipc)
        self._loading = True
        self.time_pos = None
        self.eof = False
        opts = f"start={start:.3f},loop-file={'inf' if loop else 'no'}"
        await ipc.command("loadfile", path, "replace", -1, opts)
        # Also after: some builds reset audio props per file.
        await self._reapply_audio(ipc)
        await ipc.set_property("pause", False)
        self.paused = False

    async def _sync_audio_after_launch(self, ipc: MpvIpc) -> None:
        # apply_audio during startup only stores; args() was already snapshotted at spawn.
        if (self._volume, self._audio_delay_s) != self._launched_audio:
            await self._reapply_audio(ipc)
            self._launched_audio = (self._volume, self._audio_delay_s)

    async def _reapply_audio(self, ipc: MpvIpc) -> None:
        try:
            if self._volume is not None:
                await ipc.set_property("volume", self._faded_volume())
            if self._audio_delay_s is not None:
                await ipc.set_property("audio-delay", self._audio_delay_s)
        except MpvError as exc:
            log.warning("cannot apply audio state: %s", exc)

    async def apply_audio(self, volume: float, delay_s: float) -> None:
        self._volume = volume
        self._audio_delay_s = delay_s
        ipc = self._ipc
        # Not running yet: the stored values are applied once mpv is up.
        if not self.cfg.enabled or not self.ready.is_set() or ipc is None:
            return
        await self._reapply_audio(ipc)

    async def set_audio_device(self, name: str | None) -> None:
        previous = self.audio_device_override
        self.audio_device_override = name
        ipc = self._ipc
        if not self.cfg.enabled or not self.ready.is_set() or ipc is None:
            return
        try:
            await ipc.set_property("audio-device", name or "auto")
        except MpvError as exc:
            # A rejected device must not leak into the next restart's args.
            self.audio_device_override = previous
            log.warning("audio device %s rejected by mpv: %s", name, exc)
            self.bus.event("warn", f"audio device {name} rejected by mpv")

    async def audio_devices(self) -> list[dict[str, str]]:
        ipc = self._ipc
        if not self.cfg.enabled or not self.ready.is_set() or ipc is None:
            return []
        try:
            raw = await ipc.get_property("audio-device-list")
        except Exception as exc:  # noqa: BLE001
            log.debug("audio-device-list failed: %s", exc)
            return []
        return parse_audio_devices(raw) if isinstance(raw, list) else []

    async def black(self) -> None:
        if not self.cfg.enabled:
            return
        ipc = await self._ready_ipc()
        # With --idle and --force-window, "stop" leaves an empty black window up.
        await ipc.command("stop")

    async def seek(self, t: float) -> None:
        if not self.cfg.enabled:
            return
        ipc = await self._ready_ipc()
        await ipc.command("seek", t, "absolute+exact")

    async def set_pause(self, p: bool) -> None:
        if not self.cfg.enabled:
            return
        ipc = await self._ready_ipc()
        await ipc.set_property("pause", p)

    # ---- transition fade ---------------------------------------------------------------------

    def _faded_volume(self) -> float:
        return round((self._volume or 0.0) * self.fade, 2)

    def _faded_brightness(self, b: int) -> int:
        # -100 is black whatever the geometry's own brightness is.
        return round(-100 + (b + 100) * self.fade)

    async def set_fade(self, level: float) -> None:
        """Picture and sound level for transitions; 1 restores geometry brightness and volume.

        Also applies to the next load(), so a file can start black and silent."""
        self.fade = max(0.0, min(1.0, level))
        ipc = self._ipc
        if not self.cfg.enabled or not self.ready.is_set() or ipc is None:
            return
        await self._send_geometry(ipc)
        if self._volume is not None:
            try:
                await ipc.set_property("volume", self._faded_volume())
            except MpvError as exc:
                log.debug("fade volume: %s", exc)

    # ---- display geometry --------------------------------------------------------------------

    def _run_geometry_hook(self, path: str) -> None:
        if self.geometry_for is None:
            return
        try:
            g, size = self.geometry_for(path)
        except Exception:
            log.exception("geometry hook failed for %s", path)
            return
        self.geometry, self._geom_size = g, size

    def apply_geometry(self, g: DisplayGeometry, size: tuple[int, ...] | None = None) -> None:
        """Set the target geometry; changes reach mpv coalesced (slider drags flood otherwise).

        `size` (coded w, h[, cw rotation]) is needed for crop; None keeps the loaded file's."""
        self.geometry = g
        if size is not None:
            self._geom_size = size
        if not self.cfg.enabled or self._geom_flush is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Called from a worker thread: kept as target, sent with the next load/restart.
            log.warning("apply_geometry outside the event loop; deferred")
            return
        self._geom_flush = loop.call_later(GEOMETRY_COALESCE_S, self._flush_soon)

    def _flush_soon(self) -> None:
        self._geom_flush = None
        spawn(self.flush_geometry())

    async def flush_geometry(self) -> None:
        """Send pending geometry now (no-op when mpv is not up; restart re-sends it)."""
        if self._geom_flush is not None:
            self._geom_flush.cancel()
            self._geom_flush = None
        ipc = self._ipc
        if not self.cfg.enabled or not self.ready.is_set() or ipc is None:
            return
        await self._send_geometry(ipc)

    async def _send_geometry(self, ipc: MpvIpc) -> None:
        f = SourceFrame(*self._geom_size) if self._geom_size else SourceFrame(0, 0)
        props, shader = to_mpv(self.geometry, f.w, f.h, f.rotate)
        props["brightness"] = self._faded_brightness(props["brightness"])
        # Serialised: two overlapping flushes would each diff against a stale "sent" state.
        async with self._geom_lock:
            for name, value in props.items():
                if self._sent_props.get(name) == value:
                    continue
                try:
                    await ipc.set_property(name, value)
                    self._sent_props[name] = value
                except MpvError as exc:
                    log.warning("mpv rejected %s=%r: %s", name, value, exc)
            if shader != self._sent_shader:
                try:
                    # Whole map in one go: one IPC round trip instead of one per parameter.
                    await ipc.set_property("glsl-shader-opts", shader)
                    self._sent_shader = dict(shader)
                except MpvError as exc:
                    log.warning("mpv rejected glsl-shader-opts: %s", exc)
