"""Composition root: builds and owns all long-lived services."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx

import spookhub
from spookhub import backup, db, sdnotify
from spookhub.auth import AuthStore
from spookhub.boards.client import WledClient
from spookhub.boards.discovery import MdnsDiscovery, scan_subnet
from spookhub.boards.registry import BoardManager
from spookhub.config import Config
from spookhub.events import EventBus, EventWriter
from spookhub.models import RuntimeSettings
from spookhub.player.process import MpvPlayer
from spookhub.settings_store import SettingsStore
from spookhub.show.engine import ShowEngine
from spookhub.show.preflight import Preflight
from spookhub.tasks import spawn

log = logging.getLogger(__name__)


def load_version(path: Path | None = None) -> dict[str, object]:
    """Read install metadata written by install.sh; a dev checkout has none."""
    p = path or Path(spookhub.__file__).resolve().parents[2] / "version.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"commit": "dev"}
    if not isinstance(data, dict) or "commit" not in data:
        return {"commit": "dev"}
    return data


class Hub:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.bus = EventBus()
        self.events = EventWriter(self.bus)
        self.settings = SettingsStore()
        # Many boards × short timeouts: keep connections alive, generous pool.
        self.http = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=20)
        )
        self.wled = WledClient(self.http)
        self.boards = BoardManager(
            cfg.timing,
            self.bus,
            self.wled,
            master=lambda: min(
                self.settings.value.master_brightness, self.settings.value.brightness_cap
            ),
            health_interval=lambda: self.settings.value.health_interval_s,
        )
        self.player = MpvPlayer(cfg.mpv, self.bus)
        self.engine = ShowEngine(
            self.boards, self.player, self.bus, self.settings, tick_s=cfg.timing.scheduler_tick_s
        )
        self.preflight = Preflight(self)
        self.auth = AuthStore()
        self.version = load_version()
        # media id -> {state: running|failed, progress, error}
        self.transcodes: dict[int, dict[str, object]] = {}
        self.mdns: MdnsDiscovery | None = None
        self._scan: asyncio.Task[int] | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self.settings.listeners.append(self._on_settings)

    async def start(self) -> None:
        if backup.apply_staged_restore(self.cfg.paths.db_path):
            log.warning("restored database from backup")
        db.init_engine(self.cfg.paths.db_path)
        self.auth.load()
        await self.events.start()
        self.cfg.paths.media_dir.mkdir(parents=True, exist_ok=True)
        s = self.settings.load()
        logging.getLogger("spookhub").setLevel(s.log_level.upper())
        if s.mpv_vo:
            self.player.vo_override = s.mpv_vo
        if s.mpv_gpu_context:
            self.player.gpu_context_override = s.mpv_gpu_context
        self.player.drm_connector_override = s.mpv_drm_connector
        self.player.drm_mode_override = s.mpv_drm_mode
        self.player.audio_device_override = s.mpv_audio_device
        await self.player.apply_audio(min(s.master_volume, s.volume_cap), s.audio_delay_s)
        self.boards.load_from_db()
        await self.boards.start()
        await self.player.start()
        if self.cfg.mdns_enabled:
            try:
                self.mdns = MdnsDiscovery(self.boards)
                await self.mdns.start()
            except Exception:  # noqa: BLE001
                log.exception("mDNS discovery unavailable; use scan/manual add")
        await self.engine.start()
        await self.preflight.start()
        self.bus.event("info", "Spookhub started")
        interval = sdnotify.watchdog_interval_s()
        if interval is not None:
            self._watchdog = asyncio.create_task(sdnotify.watchdog_loop(interval))
        sdnotify.notify("READY=1")

    async def stop(self) -> None:
        try:
            if self._watchdog:
                self._watchdog.cancel()
                self._watchdog = None
            await self.preflight.stop()
            await self.engine.stop()
            if self.mdns:
                await self.mdns.stop()
            await self.boards.stop()
            await self.player.stop()
            await self.http.aclose()
        finally:
            await self.events.stop()

    def scan(self) -> bool:
        if self._scan and not self._scan.done():
            return False
        self._scan = asyncio.create_task(scan_subnet(self.boards, self.cfg.timing.scan_concurrency))
        return True

    def _on_settings(self, old: RuntimeSettings, new: RuntimeSettings) -> None:
        if (old.master_brightness, old.brightness_cap) != (
            new.master_brightness,
            new.brightness_cap,
        ):
            self.boards.push_all()
        if old.log_level != new.log_level:
            logging.getLogger("spookhub").setLevel(new.log_level.upper())
        restart = False  # one restart even if several display settings change together
        if (old.mpv_vo, old.mpv_gpu_context) != (new.mpv_vo, new.mpv_gpu_context):
            self.player.vo_override = new.mpv_vo
            self.player.gpu_context_override = new.mpv_gpu_context
            restart = True
        if (old.mpv_drm_connector, old.mpv_drm_mode) != (new.mpv_drm_connector, new.mpv_drm_mode):
            self.player.drm_connector_override = new.mpv_drm_connector
            self.player.drm_mode_override = new.mpv_drm_mode
            restart = True
        if restart:
            spawn(self.player.restart())
        if old.mpv_audio_device != new.mpv_audio_device:
            spawn(self.player.set_audio_device(new.mpv_audio_device))
        audio_old = (min(old.master_volume, old.volume_cap), old.audio_delay_s)
        audio_new = (min(new.master_volume, new.volume_cap), new.audio_delay_s)
        if audio_old != audio_new:
            spawn(self.player.apply_audio(*audio_new))
        self.bus.publish("settings", new.model_dump())
