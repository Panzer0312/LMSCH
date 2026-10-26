"""Composition root: builds and owns all long-lived services."""

from __future__ import annotations

import asyncio
import logging

import httpx

from spookhub import db
from spookhub.boards.client import WledClient
from spookhub.boards.discovery import MdnsDiscovery, scan_subnet
from spookhub.boards.registry import BoardManager
from spookhub.config import Config
from spookhub.events import EventBus
from spookhub.models import RuntimeSettings
from spookhub.player.process import MpvPlayer
from spookhub.settings_store import SettingsStore
from spookhub.show.engine import ShowEngine

log = logging.getLogger(__name__)


class Hub:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.bus = EventBus()
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
            master=lambda: self.settings.value.master_brightness,
            health_interval=lambda: self.settings.value.health_interval_s,
        )
        self.player = MpvPlayer(cfg.mpv, self.bus)
        self.engine = ShowEngine(
            self.boards, self.player, self.bus, self.settings, tick_s=cfg.timing.scheduler_tick_s
        )
        self.mdns: MdnsDiscovery | None = None
        self._scan: asyncio.Task[int] | None = None
        self.settings.listeners.append(self._on_settings)

    async def start(self) -> None:
        db.init_engine(self.cfg.paths.db_path)
        self.cfg.paths.media_dir.mkdir(parents=True, exist_ok=True)
        s = self.settings.load()
        logging.getLogger("spookhub").setLevel(s.log_level.upper())
        if s.mpv_vo:
            self.player.vo_override = s.mpv_vo
        if s.mpv_gpu_context:
            self.player.gpu_context_override = s.mpv_gpu_context
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
        self.bus.event("info", "Spookhub started")

    async def stop(self) -> None:
        await self.engine.stop()
        if self.mdns:
            await self.mdns.stop()
        await self.boards.stop()
        await self.player.stop()
        await self.http.aclose()

    def scan(self) -> bool:
        if self._scan and not self._scan.done():
            return False
        self._scan = asyncio.create_task(scan_subnet(self.boards, self.cfg.timing.scan_concurrency))
        return True

    def _on_settings(self, old: RuntimeSettings, new: RuntimeSettings) -> None:
        if old.master_brightness != new.master_brightness:
            self.boards.push_all()
        if old.log_level != new.log_level:
            logging.getLogger("spookhub").setLevel(new.log_level.upper())
        if (old.mpv_vo, old.mpv_gpu_context) != (new.mpv_vo, new.mpv_gpu_context):
            self.player.vo_override = new.mpv_vo
            self.player.gpu_context_override = new.mpv_gpu_context
            asyncio.create_task(self.player.restart())
        self.bus.publish("settings", new.model_dump())
