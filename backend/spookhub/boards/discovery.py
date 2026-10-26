"""Board discovery: continuous mDNS browsing plus an on-demand /24 subnet scan."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from spookhub.boards.registry import BoardManager

log = logging.getLogger(__name__)
SERVICE = "_wled._tcp.local."


class MdnsDiscovery:
    def __init__(self, manager: BoardManager) -> None:
        self.manager = manager
        self._zc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._zc = AsyncZeroconf()
        self._browser = AsyncServiceBrowser(self._zc.zeroconf, SERVICE, handlers=[self._on_change])

    async def stop(self) -> None:
        if self._browser:
            await self._browser.async_cancel()
        if self._zc:
            await self._zc.async_close()

    def _on_change(
        self,
        zeroconf: object,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            t = asyncio.create_task(self._resolve(service_type, name))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def _resolve(self, service_type: str, name: str) -> None:
        assert self._zc
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(self._zc.zeroconf, 3000):
            return
        for addr in info.parsed_addresses():
            if ":" in addr:
                continue
            ip = addr if info.port in (None, 80) else f"{addr}:{info.port}"
            if await self.manager.probe(ip, "mDNS"):
                return


def local_subnets() -> list[ipaddress.IPv4Network]:
    """Best-effort hub /24. Connecting a UDP socket sends nothing but picks the route."""
    nets: list[ipaddress.IPv4Network] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        nets.append(ipaddress.ip_network(f"{ip}/24", strict=False))
    except OSError:
        pass
    return nets


async def scan_subnet(manager: BoardManager, concurrency: int) -> int:
    nets = local_subnets()
    sem = asyncio.Semaphore(concurrency)
    found = 0

    async def one(ip: str) -> None:
        nonlocal found
        async with sem:
            if await manager.probe(ip, "scan"):
                found += 1

    hosts = [str(h) for net in nets for h in net.hosts()]
    manager.bus.event("info", f"Scanning {', '.join(map(str, nets)) or 'nothing'}…")
    await asyncio.gather(*(one(h) for h in hosts))
    manager.bus.event("info", f"Scan done: {found} WLED board(s) answered")
    return found
