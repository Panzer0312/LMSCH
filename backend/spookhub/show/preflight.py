"""Pre-show checks: everything that would embarrass you at the first trigger."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from spookhub import db
from spookhub.boards.registry import BoardStatus
from spookhub.models import DisplayGeometry, Media, PlaybackMode, Profile, ProfileType
from spookhub.player.geometry import needs_warp

if TYPE_CHECKING:
    from spookhub.hub import Hub

log = logging.getLogger(__name__)

Status = Literal["ok", "warn", "fail"]
INTERVAL_S = 10.0
LATENCY_WARN_MS = 100.0
MIN_FREE_BYTES = 2 * 1024**3
TRIGGER_STALE_S = 30 * 60
GOOD_CODECS = {"h264", "hevc"}
MAX_HEIGHT = 1080
RESTART_GRACE_S = 15.0
WIFI_CACHE_MAX_S = 6 * 3600
VOLATILE = {"latency", "disk", "trigger_recent"}


class Check(BaseModel):
    id: str
    label: str
    status: Status
    detail: str
    fix: str | None = None


def _c(id_: str, label: str, status: Status, detail: str, fix: str | None = None) -> Check:
    return Check(id=id_, label=label, status=status, detail=detail, fix=fix)


async def ntp_synchronized() -> bool | None:
    """None when timedatectl is unavailable (containers, non-systemd)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "timedatectl", "show", "-p", "NTPSynchronized", "--value",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
    except OSError:
        return None
    try:
        async with asyncio.timeout(3):
            out, _ = await proc.communicate()
    except BaseException as e:
        # Timeout or cancel: never leave a hung child behind.
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await asyncio.shield(proc.wait())
        if isinstance(e, TimeoutError):
            return None
        raise
    if proc.returncode != 0:
        return None
    return out.decode().strip() == "yes"


class Preflight:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self.last: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._run_lock = asyncio.Lock()
        self._last_sig: tuple[Any, ...] | None = None
        self._started = time.time()
        # Seams for tests.
        self.free_bytes: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free
        self.clock_synced: Callable[[], Awaitable[bool | None]] = ntp_synchronized
        self.now: Callable[[], float] = time.time

    # ----- lifecycle -----

    async def start(self) -> None:
        self._started = self.now()
        self._task = asyncio.create_task(self._loop(), name="preflight")

    async def stop(self) -> None:
        t, self._task = self._task, None
        if t:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.run()
            except Exception:  # noqa: BLE001
                log.exception("preflight run failed")
            await asyncio.sleep(INTERVAL_S)

    async def run(self) -> dict[str, Any]:
        async with self._run_lock:
            return await self._run()

    async def _run(self) -> dict[str, Any]:
        checks: list[Check] = []
        for fn in (
            self._boards_online,
            self._latency,
            self._wifi_sleep,
            self._mpv,
            self._warp,
            self._audio_device,
            self._media,
            self._disk,
            self._clock,
            self._mpv_restarts,
            self._trigger_recent,
            self._setup_idle,
        ):
            try:
                checks.append(await fn())
            except Exception as e:  # noqa: BLE001
                log.exception("preflight check %s failed", fn.__name__)
                checks.append(
                    _c(
                        fn.__name__.lstrip("_"),
                        fn.__name__.lstrip("_"),
                        "warn",
                        f"check error: {e}",
                    )
                )
        order = {"ok": 0, "warn": 1, "fail": 2}
        worst = max((c.status for c in checks), key=order.__getitem__, default="ok")
        result = {"checks": [c.model_dump() for c in checks], "worst": worst}
        # Detail text carries live numbers (latency, minutes); only state changes are news.
        sig = (
            tuple(
                (
                    c.id,
                    c.status,
                    c.fix,
                    c.detail if c.status != "ok" and c.id not in VOLATILE else "",
                )
                for c in checks
            ),
            worst,
        )
        self.last = result
        if sig != self._last_sig:
            self._last_sig = sig
            self.hub.bus.publish("preflight", result)
        return result

    # ----- checks -----

    async def _boards_online(self) -> Check:
        label = "Boards online"
        boards = self.hub.boards.adopted()
        if not boards:
            return _c("boards_online", label, "warn", "no boards adopted", "/boards")
        off = [b.name or b.mac for b in boards if b.status == BoardStatus.offline]
        if off:
            return _c("boards_online", label, "fail", "offline: " + ", ".join(off), "/boards")
        deg = [b.name or b.mac for b in boards if b.status == BoardStatus.degraded]
        if deg:
            return _c("boards_online", label, "warn", "degraded: " + ", ".join(deg), "/boards")
        return _c("boards_online", label, "ok", f"{len(boards)} online")

    async def _latency(self) -> Check:
        label = "Board latency"
        slow = [
            f"{b.name or b.mac} {b.latency_ms:.0f} ms"
            for b in self.hub.boards.adopted()
            if b.status != BoardStatus.offline
            and b.latency_ms is not None
            and b.latency_ms >= LATENCY_WARN_MS
        ]
        if slow:
            return _c("latency", label, "warn", "slow: " + ", ".join(slow), "/boards")
        return _c("latency", label, "ok", f"all under {LATENCY_WARN_MS:.0f} ms")

    async def _wifi_sleep(self) -> Check:
        # Only the cached reading from the board recommendations check; never poll boards here.
        label = "WiFi sleep off"
        now = self.now()
        known = [
            b
            for b in self.hub.boards.adopted()
            if b.wifi_sleep is not None
            and b.wifi_sleep_at is not None
            and now - b.wifi_sleep_at < WIFI_CACHE_MAX_S
        ]
        if not known:
            return _c("wifi_sleep", label, "ok", "not checked", "/boards")
        bad = [b.name or b.mac for b in known if b.wifi_sleep]
        if bad:
            return _c("wifi_sleep", label, "warn", "sleep on: " + ", ".join(bad), "/boards")
        return _c("wifi_sleep", label, "ok", f"off on {len(known)} boards")

    async def _mpv(self) -> Check:
        label = "Video player"
        player = self.hub.engine.player
        if not player.cfg.enabled:
            return _c("mpv", label, "ok", "disabled")
        if not player.ready.is_set():
            began = player.restart_began
            if began is not None and time.monotonic() - began < RESTART_GRACE_S:
                return _c("mpv", label, "warn", "restarting", "/setup")
            return _c("mpv", label, "fail", "mpv is not running", "/setup")
        return _c("mpv", label, "ok", "running")

    async def _warp(self) -> Check:
        # vo=gpu ignores the warp shader: keystone/mask/flip would silently not show.
        label = "Keystone / mask"
        eng = self.hub.engine
        g = DisplayGeometry.model_validate(eng.display_state()["geometry"])
        if not needs_warp(g):
            return _c("warp", label, "ok", "not used")
        if not eng.warp_supported():
            return _c("warp", label, "warn", "Keystone/mask need vo=gpu-next", "/setup")
        return _c("warp", label, "ok", "active")

    async def _audio_device(self) -> Check:
        label = "Audio device"
        dev = self.hub.settings.value.mpv_audio_device
        if not dev:
            return _c("audio_device", label, "ok", "default")
        names = {d["name"] for d in await self.hub.engine.player.audio_devices()}
        if not names:
            return _c("audio_device", label, "ok", "device list unavailable")
        if dev not in names:
            return _c("audio_device", label, "warn", f"{dev} not found", "/setup")
        return _c("audio_device", label, "ok", dev)

    def _active_media(self) -> tuple[bool, list[tuple[str, Media | None]]]:
        """(video profile active, [(role, row or None)]) for the media it references."""
        a = self.hub.engine.active
        if a is None or a.type != ProfileType.video:
            return False, []
        with db.session() as s:
            p = s.get(Profile, a.id)
            if p is None:
                return False, []
            out: list[tuple[str, Media | None]] = []
            for role, mid in (("media", p.media_id), ("idle media", p.idle_media_id)):
                if mid is not None:
                    out.append((role, s.get(Media, mid)))
            return True, out

    async def _media(self) -> Check:
        label = "Media files"
        video, rows = self._active_media()
        if not video:
            return _c("media", label, "ok", "no video profile active")
        gone = [f"{role} record" for role, m in rows if m is None]
        gone += [m.filename for _, m in rows if m and not Path(m.path).exists()]
        if gone:
            return _c("media", label, "fail", "missing: " + ", ".join(gone), "/profiles")
        for _, m in rows:
            if m is None:
                continue
            if m.codec and m.codec.lower() not in GOOD_CODECS:
                return _c("media", label, "warn", f"{m.filename}: codec {m.codec}", "/profiles")
            if m.height > MAX_HEIGHT:
                return _c("media", label, "warn", f"{m.filename}: {m.height}p", "/profiles")
        return _c("media", label, "ok", "files present")

    async def _disk(self) -> Check:
        label = "Disk space"
        d = self.hub.cfg.paths.data_dir
        try:
            free = await asyncio.to_thread(self.free_bytes, d)
        except OSError:
            return _c("disk", label, "ok", "unknown")
        gb = free / 1024**3
        if free < MIN_FREE_BYTES:
            return _c("disk", label, "warn", f"{gb:.1f} GB free", "/media")
        return _c("disk", label, "ok", f"{gb:.1f} GB free")

    async def _clock(self) -> Check:
        label = "Clock synced"
        synced = await self.clock_synced()
        if synced is None:
            return _c("clock", label, "ok", "unknown")
        if not synced:
            return _c("clock", label, "warn", "NTP not synchronized")
        return _c("clock", label, "ok", "synchronized")

    async def _mpv_restarts(self) -> Check:
        label = "Player stability"
        now = self.now()
        n = sum(1 for t in self.hub.engine.player.restart_times if now - t < 3600)
        if n >= 3:
            return _c("mpv_restarts", label, "fail", f"{n} restarts in the last hour", "/setup")
        if n >= 1:
            return _c("mpv_restarts", label, "warn", f"{n} restart in the last hour", "/setup")
        return _c("mpv_restarts", label, "ok", "no restarts")

    async def _trigger_recent(self) -> Check:
        label = "Trigger seen"
        a = self.hub.engine.active
        if a is None or a.mode != PlaybackMode.idle_trigger:
            return _c("trigger_recent", label, "ok", "not needed")
        # Only accepted external triggers prove the sensor path; UI buttons and refusals do not.
        seen = self.hub.engine.last_external_ts
        ref = max(seen, self._started) if seen is not None else self._started
        age = self.now() - ref
        if age >= TRIGGER_STALE_S:
            return _c("trigger_recent", label, "warn", f"none in {age / 60:.0f} min", "/setup")
        return _c("trigger_recent", label, "ok", "recent" if seen is not None else "waiting")

    async def _setup_idle(self) -> Check:
        # A forgotten setup action refuses every trigger.
        label = "No setup running"
        snap = self.hub.engine.snapshot()
        busy = []
        if snap["calibrating"]:
            busy.append("calibration")  # itself the setup task, so not "setup test" too
        elif snap["setup_busy"]:
            busy.append("setup test")
        if snap["test_pattern"]:
            busy.append("test pattern")
        if busy:
            return _c("setup_idle", label, "fail", "running: " + ", ".join(busy), "/setup")
        return _c("setup_idle", label, "ok", "idle")
