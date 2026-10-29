"""Pre-show checks: everything that would embarrass you at the first trigger."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from spookhub import db
from spookhub.boards.registry import BoardStatus
from spookhub.models import Media, PlaybackMode, Profile, ProfileType

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
        async with asyncio.timeout(3):
            out, _ = await proc.communicate()
    except (OSError, TimeoutError):
        return None
    if proc.returncode != 0:
        return None
    return out.decode().strip() == "yes"


class Preflight:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self.last: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
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
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self.run()
            except Exception:  # noqa: BLE001
                log.exception("preflight run failed")
            await asyncio.sleep(INTERVAL_S)

    async def run(self) -> dict[str, Any]:
        checks: list[Check] = []
        for fn in (
            self._boards_online,
            self._latency,
            self._wifi_sleep,
            self._mpv,
            self._audio_device,
            self._media,
            self._disk,
            self._clock,
            self._mpv_restarts,
            self._trigger_recent,
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
        if result != self.last:
            self.last = result
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
        # Board configs are fetched on demand only (boards page), never cached, so this stays
        # unknown rather than hitting every board every 10 s.
        return _c("wifi_sleep", "WiFi sleep off", "ok", "not checked", "/boards")

    async def _mpv(self) -> Check:
        label = "Video player"
        player = self.hub.engine.player
        if not player.cfg.enabled:
            return _c("mpv", label, "ok", "disabled")
        if not player.ready.is_set():
            return _c("mpv", label, "fail", "mpv is not running", "/setup")
        return _c("mpv", label, "ok", "running")

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

    def _active_media(self) -> tuple[Profile | None, Media | None, Media | None]:
        a = self.hub.engine.active
        if a is None or a.type != ProfileType.video:
            return None, None, None
        with db.session() as s:
            p = s.get(Profile, a.id)
            if p is None:
                return None, None, None
            main = s.get(Media, p.media_id) if p.media_id else None
            idle = s.get(Media, p.idle_media_id) if p.idle_media_id else None
            return p, main, idle

    async def _media(self) -> Check:
        label = "Media files"
        p, main, idle = self._active_media()
        if p is None:
            return _c("media", label, "ok", "no video profile active")
        missing = [m.filename for m in (main, idle) if m and not Path(m.path).exists()]
        if missing:
            return _c("media", label, "fail", "missing: " + ", ".join(missing), "/profiles")
        if main:
            if main.codec and main.codec.lower() not in GOOD_CODECS:
                return _c(
                    "media", label, "warn", f"{main.filename}: codec {main.codec}", "/profiles"
                )
            if main.height > MAX_HEIGHT:
                return _c("media", label, "warn", f"{main.filename}: {main.height}p", "/profiles")
        return _c("media", label, "ok", "files present")

    async def _disk(self) -> Check:
        label = "Disk space"
        d = self.hub.cfg.paths.data_dir
        try:
            free = self.free_bytes(d)
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
        lt = self.hub.engine.last_trigger
        ref = max(lt["ts"], self._started) if lt else self._started
        age = self.now() - ref
        if age >= TRIGGER_STALE_S:
            return _c("trigger_recent", label, "warn", f"none in {age / 60:.0f} min", "/setup")
        return _c("trigger_recent", label, "ok", "recent" if lt else "waiting")
