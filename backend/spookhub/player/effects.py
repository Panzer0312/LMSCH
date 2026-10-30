"""Audio-only mpv instances next to the main player: `fx` (sound cues, soundboard) and `ambient`.

Each is a supervised `MpvPlayer` with `--no-video` and its own IPC socket. One sound at a time
per player; a new play replaces the old. Volume is the main player's master level (already
capped) times a per-sound gain, never above the volume cap. Fades step `volume` every ~20 ms.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from spookhub.config import MpvCfg
from spookhub.events import EventBus
from spookhub.player.ipc import MpvError, MpvIpc
from spookhub.player.process import MpvPlayer

log = logging.getLogger("spookhub.mpv.effects")

RAMP_STEP_S = 0.02
MAX_GAIN = 1.3


class EffectsPlayer(MpvPlayer):
    def __init__(self, cfg: MpvCfg, bus: EventBus, name: str) -> None:
        super().__init__(cfg, bus)
        self.name = name
        self._master: float = 100.0  # min(master_volume, volume_cap), mpv 0..130 scale
        self._cap: float = 130.0
        self.gain: float = 1.0
        # Who started the current sound (e.g. "cue"); stop(owner=...) leaves other sounds alone.
        self.owner: str | None = None
        self.current: str | None = None
        self.ramp_task: asyncio.Task[None] | None = None
        # Only one load() at a time; which call still matters is decided by `_gen`.
        self._cmd = asyncio.Lock()
        # Bumped by every play/stop: an older call that has not reached mpv yet is stale and drops
        # out, so calls piled up while mpv was down do not replay after it recovers.
        self._gen = 0
        self._load_gen = 0
        # `current` names a file mpv has not started yet; an eof now belongs to the previous one.
        self._await_start = False
        self._volume = self._level()

    # ---- lifecycle ---------------------------------------------------------------------------

    @property
    def socket_path(self) -> Path:
        p = self.cfg.socket_path
        own = p.with_name(f"{p.stem}-{self.name}{p.suffix}")
        if len(str(own)) <= 100:
            return own
        return Path(tempfile.gettempdir()) / f"spookhub-{os.getuid()}-mpv-{self.name}.sock"

    def args(self) -> list[str]:
        cfg = self.cfg
        out = [
            cfg.binary,
            "--idle=yes",
            "--keep-open=no",
            "--no-video",
            "--osc=no",
            "--no-input-default-bindings",
            "--input-terminal=no",
            "--no-terminal",
        ]
        if cfg.ao:
            out.append(f"--ao={cfg.ao}")
        if self.audio_device_override:
            out.append(f"--audio-device={self.audio_device_override}")
        if self._volume is not None:
            out.append(f"--volume={self._volume:g}")
        out.append(f"--input-ipc-server={self.socket_path}")
        out.append("--hr-seek=yes")
        # cfg.extra_args are for the projector player (drm connector, shaders); not copied.
        return out

    async def shutdown(self) -> None:
        """Process lifecycle stop (`stop()` here stops the current sound)."""
        self._cancel_ramp()
        await MpvPlayer.stop(self)

    async def _send_geometry(self, ipc: MpvIpc) -> None:
        return  # no picture

    # ---- volume ------------------------------------------------------------------------------

    def _level(self) -> float:
        return round(min(self._master * self.gain, self._cap), 2)

    async def apply_audio(self, volume: float, delay_s: float = 0.0, cap: float = 130.0) -> None:
        # audio-delay shifts audio against video; with no video it means nothing, so ignored.
        self._master, self._cap = volume, cap
        self._volume = self._level()
        self._audio_delay_s = None
        await self._send_volume()

    async def _send_volume(self) -> None:
        self._volume = self._level()
        ipc = self._ipc
        if not self.cfg.enabled or not self.ready.is_set() or ipc is None:
            return
        try:
            await ipc.set_property("volume", self._volume)
        except MpvError as exc:
            log.debug("%s volume: %s", self.name, exc)

    def _cancel_ramp(self) -> None:
        t = self.ramp_task
        self.ramp_task = None
        if t is not None and not t.done() and t is not asyncio.current_task():
            t.cancel()

    def ramp(self, volume: float, ms: int, then_stop: bool = False) -> asyncio.Task[None]:
        """Fade the gain to `volume` over `ms`; a new ramp cancels the running one."""
        self._cancel_ramp()
        self.ramp_task = asyncio.create_task(
            self._ramp(max(0.0, min(volume, MAX_GAIN)), ms, then_stop)
        )
        return self.ramp_task

    async def _ramp(self, target: float, ms: int, then_stop: bool) -> None:
        start = self.gain
        steps = max(1, round(ms / 1000 / RAMP_STEP_S))
        for i in range(1, steps + 1):
            self.gain = start + (target - start) * i / steps
            await self._send_volume()
            if i < steps:
                await asyncio.sleep(RAMP_STEP_S)
        if then_stop:
            await self._halt()

    # ---- playback ----------------------------------------------------------------------------

    async def play(
        self,
        path: str,
        start: float = 0.0,
        volume: float = 1.0,
        fade_in_ms: int = 0,
        loop: bool = False,
        owner: str | None = None,
    ) -> None:
        volume = max(0.0, min(volume, MAX_GAIN))
        self._gen += 1
        gen = self._gen
        self._cancel_ramp()
        # Set now, not after load(): stop() must be able to cancel a play still in flight.
        self.owner, self.current = owner, path
        self._await_start = True
        try:
            if self.cfg.enabled:
                # Wait for mpv outside the lock so a newer call is not queued behind a dead mpv.
                await self._ready_ipc()
            if gen != self._gen:
                return
            async with self._cmd:
                if gen != self._gen:
                    return
                self.gain = 0.0 if fade_in_ms > 0 else volume
                self._volume = self._level()
                self._load_gen = gen
                # load() re-asserts self._volume before the first samples.
                await self.load(path, start=max(0.0, start), loop=loop)
                if gen != self._gen:
                    # A stop landed while loadfile was in flight; its mpv `stop` may have gone out
                    # first. A newer play replaces the file anyway, so only halt if none is pending.
                    if self.current is None:
                        await self._halt()
                    return
        except MpvError:
            if gen == self._gen:
                self.current = self.owner = None
                self._await_start = False
                raise
            return
        if fade_in_ms > 0:
            self.ramp(volume, fade_in_ms)

    async def stop(self, fade_out_ms: int = 0, owner: str | None = None) -> None:  # type: ignore[override]
        """Stop the current sound. With `owner`, only if that owner started it."""
        if owner is not None and self.owner != owner:
            return
        if self.current is None:
            return
        self._gen += 1
        if fade_out_ms > 0:
            self.ramp(0.0, fade_out_ms, then_stop=True)
            return
        # No lock: blackout must not wait seconds on a load; the play notices it was superseded.
        self._cancel_ramp()
        await self._halt()

    async def _halt(self) -> None:
        self.current = self.owner = None
        if not self.cfg.enabled:
            return
        ipc = self._ipc
        if ipc is None or not self.ready.is_set():
            return
        with contextlib.suppress(MpvError):
            await ipc.command("stop")

    def _on_event(self, msg: dict[str, Any]) -> None:
        # keep-open=no: a finished sound ends with reason "eof"; a replaced one with "stop".
        ev = msg.get("event")
        if ev == "start-file":
            # A superseded load's file starting does not mean the newest one has.
            if self._load_gen == self._gen:
                self._await_start = False
        elif ev == "end-file" and msg.get("reason") == "eof" and not self._await_start:
            self.current = self.owner = None
        super()._on_event(msg)

    def _reset_state(self) -> None:
        super()._reset_state()
        # A restarted mpv plays nothing.
        self.current = self.owner = None
        self._await_start = False
