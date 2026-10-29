"""Phase 1 task 6: stall watchdog, sd_notify, board fallback."""

import asyncio
import socket
import time

import pytest

from spookhub import sdnotify
from spookhub.models import BoardSelection, LightState, PlaybackMode, ProfileType
from spookhub.show.engine import ActiveProfile, Phase
from tests.conftest import wait_for
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_calibration import _adopt
from tests.test_setup import FakePlayer


class StallPlayer(FakePlayer):
    def __init__(self):
        super().__init__()
        self.loads = []
        self.restarts = 0

    async def load(self, path, start=0.0, loop=False):
        await super().load(path, start, loop)
        self.loads.append((path, start, loop))
        self.time_pos = None  # like mpv: unknown until the new file reports

    async def restart(self):
        self.restarts += 1
        self.time_pos = None
        asyncio.get_running_loop().call_soon(self._notify, "restart")  # mpv back up


def _video_prof(mode=PlaybackMode.once):
    return ActiveProfile(
        id=1, name="v", type=ProfileType.video, selection=BoardSelection(all=True),
        base=LightState(on=True), overrides={}, mode=mode, cooldown_s=0,
        media_path="/m.mp4", media_duration=30, idle_path=None, cues=[],
    )  # fmt: skip


@pytest.fixture
def stall(client):  # noqa: F811
    eng = client.hub.engine
    p = StallPlayer()
    eng.player = p
    p.on_change = eng._on_player_change
    eng.stall_s, eng.stall_poll_s = 0.15, 0.02
    eng.active = _video_prof()
    eng.timeline = None
    return eng, p


def _tick_pos(p, t):
    p.time_pos, p.pos_mono = t, time.monotonic()


async def test_stall_restarts_and_resumes_then_second_stall_ends(stall):
    from spookhub.show.resolve import Timeline

    eng, p = stall
    eng.timeline = Timeline([], [])
    assert await eng._play_main(0.0)
    assert eng.phase == Phase.playing
    _tick_pos(p, 5.0)
    await wait_for(lambda: p.restarts == 1)
    await wait_for(lambda: len(p.loads) == 2)
    assert p.loads[1] == ("/m.mp4", 5.0, False)
    assert eng.playing_main and eng.phase == Phase.playing  # restart was not read as a crash
    events = [e for e in eng.bus.log if e["kind"] == "player"]
    assert [e["message"] for e in events] == ["video stalled, restarting"]

    _tick_pos(p, 5.1)  # plays a bit, then freezes again
    await wait_for(lambda: not eng.playing_main)
    assert p.restarts == 1 and eng.phase == Phase.idle
    assert any("stalled" in e["message"] for e in eng.bus.log)


async def test_no_stall_while_advancing_paused_or_loading(stall):
    from spookhub.show.resolve import Timeline

    eng, p = stall
    eng.timeline = Timeline([], [])
    await eng._play_main(0.0)
    for _ in range(15):  # 0.3 s > stall_s, but time-pos keeps moving
        _tick_pos(p, p.time_pos or 1.0)
        p.time_pos += 0.02
        await asyncio.sleep(0.02)
    p.paused = True
    _tick_pos(p, 3.0)
    await asyncio.sleep(0.3)
    p.paused = False
    p._loading = True
    await asyncio.sleep(0.3)
    assert p.restarts == 0 and eng.playing_main
    await eng._end_show("stopped")


async def test_calibration_style_scheduler_has_no_stall_watch(stall):
    eng, p = stall
    assert eng._stall_task is None  # only _play_main starts it


def test_sd_notify_unset_is_noop(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert sdnotify.notify("READY=1") is False


@pytest.mark.parametrize("abstract", [False, True])
def test_sd_notify_datagram(monkeypatch, tmp_path, abstract):
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as srv:
        if abstract:
            name = f"spookhub-test-{time.monotonic_ns()}"
            srv.bind("\0" + name)
            monkeypatch.setenv("NOTIFY_SOCKET", "@" + name)
        else:
            path = str(tmp_path / "n.sock")
            srv.bind(path)
            monkeypatch.setenv("NOTIFY_SOCKET", path)
        srv.settimeout(2)
        assert sdnotify.notify("READY=1") is True
        assert srv.recv(64) == b"READY=1"


def test_watchdog_interval(monkeypatch):
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert sdnotify.watchdog_interval_s() is None
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")
    assert sdnotify.watchdog_interval_s() == 15.0


async def test_hub_pings_watchdog_and_stops(cfg, monkeypatch, tmp_path):
    from asgi_lifespan import LifespanManager

    from spookhub.main import create_app

    path = str(tmp_path / "n.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(path)
    srv.setblocking(False)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    monkeypatch.setenv("WATCHDOG_USEC", "100000")  # ping every 50 ms
    app = create_app(cfg)
    got: list[bytes] = []

    def drain():
        while True:
            try:
                got.append(srv.recv(64))
            except BlockingIOError:
                return

    async with LifespanManager(app):
        await wait_for(lambda: (drain(), b"WATCHDOG=1" in got)[1])
        assert got[0] == b"READY=1"
    drain()
    got.clear()
    await asyncio.sleep(0.2)
    drain()
    assert got == []  # pings stopped with the hub
    srv.close()


async def test_fallback_payloads_one_and_all(client, farm):  # noqa: F811
    macs = await _adopt(client, farm, 2)
    a, b = farm.boards
    r = await client.post(
        "/api/profiles",
        json={
            "name": "Red",
            "base_state": {"on": True, "bri": 180, "colors": [[255, 0, 0]]},
            "selection": {"all": False, "macs": [macs[0]]},
        },
    )
    pid = r.json()["id"]
    await client.post(f"/api/profiles/{pid}/activate")
    await wait_for(lambda: a.state["bri"] == 180)
    await client.put("/api/settings", json={"master_brightness": 0.5})
    await wait_for(lambda: a.state["bri"] == 90)
    for fb in farm.boards:
        fb.log.clear()

    r = await client.post("/api/boards/fallback")
    assert r.status_code == 200
    res = r.json()["results"]
    assert [x["mac"] for x in res] == [macs[0]] and res[0]["ok"] and res[0]["error"] is None
    bodies = [e["body"] for e in a.log]
    assert bodies[0]["bri"] == 180  # base at full brightness, not master-scaled
    assert bodies[1] == {"psave": 250, "n": "Spookhub fallback", "ib": True, "sb": True}
    assert a.presets["250"]["n"] == "Spookhub fallback" and a.presets["250"]["bri"] == 180
    assert a.cfg["def"] == {"ps": 250, "on": True}
    assert b.log == [] and "def" not in b.cfg

    r = await client.post(f"/api/boards/{macs[1]}/fallback")
    assert r.json()["results"][0]["ok"] and b.cfg["def"]["ps"] == 250
    assert (await client.post("/api/boards/nope/fallback")).status_code == 404


async def test_fallback_reports_error_per_board(client, farm):  # noqa: F811
    macs = await _adopt(client, farm, 2)
    farm.boards[1].dropout = True
    client.hub.boards.timing.command_timeout_s = 0.2
    res = (await client.post("/api/boards/fallback")).json()["results"]
    by = {x["mac"]: x for x in res}
    assert by[macs[0]]["ok"] and not by[macs[1]]["ok"] and by[macs[1]]["error"]
    farm.boards[1].dropout = False
