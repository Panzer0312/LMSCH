from __future__ import annotations

import asyncio
import socket
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "simulator"))

import wled_sim  # noqa: E402

from spookhub.config import Config, MpvCfg, PathsCfg, TimingCfg  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SimFarm:
    """Fake WLED boards on real local ports (so client timeouts behave like the network)."""

    def __init__(self) -> None:
        self.boards: list[wled_sim.FakeBoard] = []
        self._servers: list[tuple[uvicorn.Server, asyncio.Task[None]]] = []

    async def add(self, index: int, mac: str | None = None) -> wled_sim.FakeBoard:
        port = free_port()
        b = wled_sim.FakeBoard(index=index, port=port)
        if mac:
            b.mac = mac
        app = wled_sim.make_app(b)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        self.boards.append(b)
        self._servers.append((server, task))
        return b

    async def kill(self, b: wled_sim.FakeBoard) -> None:
        i = self.boards.index(b)
        server, task = self._servers[i]
        server.should_exit = True
        await task

    async def close(self) -> None:
        for server, _task in self._servers:
            server.should_exit = True
        await asyncio.gather(*(t for _, t in self._servers), return_exceptions=True)


@pytest.fixture
async def farm() -> AsyncIterator[SimFarm]:
    f = SimFarm()
    yield f
    await f.close()


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        paths=PathsCfg(data_dir=tmp_path / "data"),
        mpv=MpvCfg(enabled=False, socket_path=tmp_path / "mpv.sock"),
        timing=TimingCfg(health_interval_s=0.3, health_timeout_s=0.3, offline_after_failures=2),
        mdns_enabled=False,
    )


async def wait_for(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    t0 = asyncio.get_running_loop().time()
    while not pred():
        if asyncio.get_running_loop().time() - t0 > timeout:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)
