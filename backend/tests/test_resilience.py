"""Phase 1 task 6: stall watchdog, sd_notify, board fallback."""

import asyncio
import os
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
        self.seeks = []
        self.revive = True

    async def load(self, path, start=0.0, loop=False):
        await super().load(path, start, loop)
        self.loads.append((path, start, loop))
        self.time_pos = None  # like mpv: unknown until the new file reports

    async def restart(self):
        self.restarts += 1
        self.time_pos = None
        if self.revive:
            asyncio.get_running_loop().call_soon(self._notify, "restart")  # mpv back up

    async def seek(self, t):
        self.seeks.append(t)


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
    eng.stall_s, eng.stall_poll_s = 0.4, 0.05
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
    for _ in range(30):  # 0.6 s > stall_s (0.4), but time-pos keeps moving
        _tick_pos(p, (p.time_pos or 1.0) + 0.02)
        await asyncio.sleep(0.02)
    p.paused = True
    _tick_pos(p, 3.0)
    await asyncio.sleep(0.6)
    p.paused = False
    p._loading = True
    await asyncio.sleep(0.6)
    assert p.restarts == 0 and eng.playing_main
    await eng._end_show("stopped")


async def test_calibration_scheduler_has_no_stall_watch(stall):
    from spookhub.show.calibration import calibration_cues
    from spookhub.show.resolve import Timeline

    eng, p = stall
    prof = _video_prof(PlaybackMode.loop)
    prof.cues = calibration_cues()
    eng.calibrating = True
    eng._start_scheduler(prof, Timeline(prof.cues, []), eng._player_clock)
    _tick_pos(p, 3.0)
    await asyncio.sleep(1.2)  # frozen for 3x stall_s
    assert eng._stall_task is None and p.restarts == 0
    await eng._stop_scheduler()


async def test_unpause_after_long_pause_is_not_a_stall(stall):
    from spookhub.show.resolve import Timeline

    eng, p = stall
    eng.timeline = Timeline([], [])
    await eng._play_main(0.0)
    p.paused = True
    _tick_pos(p, 5.0)
    await asyncio.sleep(1.0)  # pos_mono is now far older than stall_s
    p.paused = False
    eng._on_player_change("pause")
    await asyncio.sleep(0.2)  # < stall_s since unpause
    assert p.restarts == 0
    await wait_for(lambda: p.restarts == 1)  # a real freeze after unpause is still caught
    await eng._end_show("stopped")


async def test_seek_resets_clock_and_seeking_or_cache_skip_check(stall):
    from spookhub.show.resolve import Timeline

    eng, p = stall
    eng.timeline = Timeline([], [])
    await eng._play_main(0.0)
    before = time.monotonic()
    await eng.seek(20.0)
    assert p.seeks == [20.0] and eng._resumed_mono >= before
    for flag in ("_seeking", "_buffering", "_loading"):
        setattr(p, flag, True)
        _tick_pos(p, 5.0)
        await asyncio.sleep(1.0)
        setattr(p, flag, False)
        assert p.restarts == 0, flag
        eng._resumed_mono = time.monotonic()
    assert (p.seeking, p.buffering, p.loading) == (False, False, False)
    await eng._end_show("stopped")


async def test_advancing_loop_wrap_and_eof_are_not_stalls(stall):
    from spookhub.show.resolve import Timeline

    eng, p = stall
    eng.active = _video_prof(PlaybackMode.loop)
    eng.timeline = Timeline([], [])
    await eng._play_main(0.0)
    for t in (29.0, 29.8, 0.0, 0.8, 1.6):  # wraps, each report is movement
        _tick_pos(p, t)
        await asyncio.sleep(0.25)
    p.eof = True
    _tick_pos(p, 30.0)
    await asyncio.sleep(1.0)
    assert p.restarts == 0
    p.eof = False
    await eng._end_show("stopped")


async def test_crash_restart_still_ends_show_as_interrupted(stall):
    from spookhub.show.resolve import Timeline

    eng, p = stall
    eng.timeline = Timeline([], [])
    await eng._play_main(0.0)
    p._notify("restart")  # not requested by the stall watch
    await wait_for(lambda: not eng.playing_main)
    assert any("interrupted" in e["message"] for e in eng.bus.log) and p.restarts == 0


async def test_restart_timeout_ends_show_idle(stall):
    from spookhub.show.resolve import Timeline

    eng, p = stall
    p.revive = False
    eng.stall_restart_timeout_s = 0.3
    eng.timeline = Timeline([], [])
    await eng._play_main(0.0)
    _tick_pos(p, 5.0)
    await wait_for(lambda: not eng.playing_main)
    assert p.restarts == 1 and eng.phase == Phase.idle and len(p.loads) == 1


async def test_blackout_during_recovery_wins(stall):
    from spookhub.show.resolve import Timeline

    eng, p = stall
    p.revive = False
    eng.stall_restart_timeout_s = 30
    eng.timeline = Timeline([], [])
    await eng._play_main(0.0)
    _tick_pos(p, 5.0)
    await wait_for(lambda: p.restarts == 1)
    await eng.set_blackout(True)
    p._notify("restart")  # late player-up must not resume the video
    await asyncio.sleep(0.3)
    assert eng.phase == Phase.blackout and not eng.playing_main
    assert len(p.loads) == 1 and eng._stall_task is None


async def test_resume_reapplies_cue_at_position_only(client, farm, stall):  # noqa: F811
    from spookhub.show.resolve import CueSpec, Timeline

    (mac,) = await _adopt(client, farm, 1)
    eng, p = stall
    red = LightState(on=True, colors=[(255, 0, 0)])
    blue = LightState(on=True, colors=[(0, 0, 255)])
    cues = [
        CueSpec(id=1, start=1.0, duration=2.0, targets=None, state=red, transition_ms=0),
        CueSpec(id=2, start=4.0, duration=3.0, targets=None, state=blue, transition_ms=0),
    ]
    eng.active = _video_prof()
    eng.active.cues = cues
    eng.timeline = Timeline(cues, [mac])
    sent = []
    eng.boards.set_show_state = lambda m, st, critical: sent.append(st.colors)  # type: ignore[method-assign]
    await eng._play_main(0.0)
    _tick_pos(p, 5.0)
    await wait_for(lambda: len(p.loads) == 2)
    sent.clear()
    _tick_pos(p, 5.0)  # mpv reports the resumed position
    await wait_for(lambda: bool(sent))
    assert sent[0] == [(0, 0, 255)]  # cue active at 5 s; red (2 s) is not re-fired
    assert [(255, 0, 0)] not in sent
    await eng._end_show("stopped")


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


def test_watchdog_pid_must_match(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")
    monkeypatch.setenv("WATCHDOG_PID", "1")
    assert sdnotify.watchdog_interval_s() is None
    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid()))
    assert sdnotify.watchdog_interval_s() == 15.0


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
    # Board returns to its desired state (master applied), and the preset cache learns 250.
    await wait_for(lambda: a.state["bri"] == 90 and b.state["bri"] != 0)
    await wait_for(lambda: "250" in client.hub.boards.boards[macs[0]].presets)


async def test_fallback_reports_error_per_board(client, farm, monkeypatch):  # noqa: F811
    macs = await _adopt(client, farm, 2)
    monkeypatch.setattr("spookhub.boards.registry.FALLBACK_WRITE_TIMEOUT_S", 0.3)
    for m in macs:
        client.hub.boards.set_manual(m, LightState(on=True, bri=50))
    await wait_for(lambda: all(b.state["bri"] == 50 for b in farm.boards))
    farm.boards[1].dropout = True
    res = (await client.post("/api/boards/fallback")).json()["results"]
    by = {x["mac"]: x for x in res}
    assert by[macs[0]]["ok"] and not by[macs[1]]["ok"] and by[macs[1]]["error"]
    farm.boards[1].dropout = False


async def test_fallback_refused_during_blackout_and_show(client, farm):  # noqa: F811
    await _adopt(client, farm, 1)
    sim = farm.boards[0]
    await client.post("/api/blackout", json={"on": True})
    assert (await client.post("/api/boards/fallback")).status_code == 409
    await client.post("/api/blackout", json={"on": False})
    client.hub.engine.playing_main = True
    assert (await client.post("/api/boards/fallback")).status_code == 409
    mac = client.hub.boards.adopted_macs()[0]
    assert (await client.post(f"/api/boards/{mac}/fallback")).status_code == 409
    client.hub.engine.playing_main = False
    client.hub.engine.calibrating = True
    assert (await client.post("/api/boards/fallback")).status_code == 409
    client.hub.engine.calibrating = False
    assert "250" not in sim.presets and "def" not in sim.cfg


async def test_fallback_without_profile_uses_manual_or_errors(client, farm):  # noqa: F811
    (mac,) = await _adopt(client, farm, 1)
    sim = farm.boards[0]
    sim.log.clear()
    res = (await client.post("/api/boards/fallback")).json()["results"]
    assert res[0]["ok"] is False and res[0]["error"] == "no base state"
    assert sim.log == [] and "def" not in sim.cfg  # nothing written, no psave
    client.hub.boards.set_manual(mac, LightState(on=True, bri=77))
    await wait_for(lambda: sim.state["bri"] == 77)
    res = (await client.post("/api/boards/fallback")).json()["results"]
    assert res[0]["ok"] and sim.presets["250"]["bri"] == 77


async def test_fallback_saves_base_not_inflight_push(client, farm):  # noqa: F811
    (mac,) = await _adopt(client, farm, 1)
    sim = farm.boards[0]
    r = await client.post(
        "/api/profiles", json={"name": "P", "base_state": {"on": True, "bri": 180}}
    )
    await client.post(f"/api/profiles/{r.json()['id']}/activate")
    await wait_for(lambda: sim.state["bri"] == 180)
    sim.latency_ms = 300
    client.hub.boards.set_manual(mac, LightState(bri=33))  # in flight when fallback starts
    await asyncio.sleep(0.1)
    res = (await client.post("/api/boards/fallback")).json()["results"]
    sim.latency_ms = 0
    assert res[0]["ok"] and sim.presets["250"]["bri"] == 180
    await wait_for(lambda: sim.state["bri"] == 33)  # desired state replayed afterwards


async def test_fallback_blocked_midway_refuses_remaining_boards(client, farm):  # noqa: F811
    macs = await _adopt(client, farm, 2)
    for m in macs:
        client.hub.boards.set_manual(m, LightState(on=True, bri=50))
    await wait_for(lambda: all(b.state["bri"] == 50 for b in farm.boards))
    boards = client.hub.boards
    real = boards.save_fallback

    async def save_then_show_starts(mac, state):
        err = await real(mac, state)
        client.hub.engine.playing_main = True
        return err

    boards.save_fallback = save_then_show_starts  # type: ignore[method-assign]
    res = (await client.post("/api/boards/fallback")).json()["results"]
    client.hub.engine.playing_main = False
    assert [x["ok"] for x in res] == [True, False]
    assert res[1]["error"] == "refused: a show is playing"
    assert "250" in farm.boards[0].presets and "250" not in farm.boards[1].presets
    assert "def" not in farm.boards[1].cfg
