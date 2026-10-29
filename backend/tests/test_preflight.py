"""Phase 1 task 3: preflight checks, test-all, failed setup action restore."""

import asyncio
import shutil
import time

import pytest

from spookhub import db
from spookhub.boards.registry import BoardRuntime, BoardStatus
from spookhub.models import Media
from tests.conftest import wait_for
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_setup import FakePlayer, fake  # noqa: F401  (fixtures)


def _board(hub, mac, status=BoardStatus.online, lat=10.0, adopted=True):
    b = BoardRuntime(mac=mac, ip="127.0.0.1", name=mac, adopted=adopted, status=status)
    b.latency_ms = lat
    hub.boards.boards[mac] = b
    return b


async def _check(hub, cid):
    res = await hub.preflight.run()
    return next(c for c in res["checks"] if c["id"] == cid)


async def test_check_order_and_worst(client):  # noqa: F811
    res = await client.get("/api/preflight")
    ids = [c["id"] for c in res.json()["checks"]]
    assert ids == [
        "boards_online", "latency", "wifi_sleep", "mpv", "audio_device",
        "media", "disk", "clock", "mpv_restarts", "trigger_recent",
    ]  # fmt: skip
    assert res.json()["worst"] == "warn"  # no boards adopted
    assert res.json() == (await client.get("/api/preflight")).json()


async def test_boards_online_and_latency(client):  # noqa: F811
    hub = client.hub
    assert (await _check(hub, "boards_online"))["status"] == "warn"
    _board(hub, "aa")
    assert (await _check(hub, "boards_online"))["status"] == "ok"
    b = _board(hub, "bb", BoardStatus.degraded, lat=100.0)
    assert (await _check(hub, "boards_online"))["status"] == "warn"
    assert (await _check(hub, "latency"))["status"] == "warn"
    b.status, b.latency_ms = BoardStatus.online, 99.0
    assert (await _check(hub, "latency"))["status"] == "ok"
    _board(hub, "cc", BoardStatus.offline, lat=500.0)
    c = await _check(hub, "boards_online")
    assert c["status"] == "fail" and c["fix"] == "/boards"
    assert (await _check(hub, "latency"))["status"] == "ok"  # offline boards are not "slow"
    _board(hub, "dd", adopted=False, status=BoardStatus.offline)
    assert "dd" not in c["detail"]


async def test_wifi_sleep_not_checked(client):  # noqa: F811
    c = await _check(client.hub, "wifi_sleep")
    assert c["status"] == "ok" and c["detail"] == "not checked"


async def test_mpv_check(client, fake):  # noqa: F811
    assert (await _check(client.hub, "mpv"))["status"] == "fail"
    fake.ready.set()
    assert (await _check(client.hub, "mpv"))["status"] == "ok"
    client.hub.engine.player.cfg.enabled = False
    assert (await _check(client.hub, "mpv"))["detail"] == "disabled"


async def test_audio_device_check(client, fake, monkeypatch):  # noqa: F811
    hub = client.hub

    async def devs():
        return [{"name": "auto", "description": ""}, {"name": "alsa/x", "description": ""}]

    monkeypatch.setattr(fake, "audio_devices", devs)
    assert (await _check(hub, "audio_device"))["status"] == "ok"
    hub.settings.value.mpv_audio_device = "alsa/x"
    assert (await _check(hub, "audio_device"))["status"] == "ok"
    hub.settings.value.mpv_audio_device = "alsa/gone"
    assert (await _check(hub, "audio_device"))["status"] == "warn"

    async def none():
        return []

    monkeypatch.setattr(fake, "audio_devices", none)
    assert (await _check(hub, "audio_device"))["status"] == "ok"


async def _video_profile(client, path, codec="h264", height=1080, idle=False):  # noqa: F811
    with db.session() as s:
        m = Media(filename="v.mp4", path=str(path), duration_s=5, codec=codec, height=height)
        s.add(m)
        s.commit()
        s.refresh(m)
    body = {"name": "V", "type": "video", "media_id": m.id, "base_state": {"on": True}}
    if idle:
        body["idle_media_id"] = m.id
    r = await client.post("/api/profiles", json=body)
    await client.hub.engine.activate(r.json()["id"], autostart=False)


async def test_media_check(client, tmp_path, fake):  # noqa: F811
    hub = client.hub
    assert (await _check(hub, "media"))["status"] == "ok"
    f = tmp_path / "v.mp4"
    await _video_profile(client, f)
    assert (await _check(hub, "media"))["status"] == "fail"
    f.write_bytes(b"x")
    assert (await _check(hub, "media"))["status"] == "ok"
    with db.session() as s:
        m = s.get(Media, 1)
        m.codec = "vp9"
        s.add(m)
        s.commit()
    assert (await _check(hub, "media"))["status"] == "warn"
    with db.session() as s:
        m = s.get(Media, 1)
        m.codec, m.height = "hevc", 2160
        s.add(m)
        s.commit()
    assert (await _check(hub, "media"))["status"] == "warn"


async def test_disk_check(client):  # noqa: F811
    pf = client.hub.preflight
    pf.free_bytes = lambda p: 1 * 1024**3
    assert (await _check(client.hub, "disk"))["status"] == "warn"
    pf.free_bytes = lambda p: 5 * 1024**3
    assert (await _check(client.hub, "disk"))["status"] == "ok"

    def boom(p):
        raise OSError

    pf.free_bytes = boom
    assert (await _check(client.hub, "disk"))["detail"] == "unknown"


async def test_clock_check(client):  # noqa: F811
    pf = client.hub.preflight
    for val, want in [(True, "ok"), (False, "warn"), (None, "ok")]:

        async def f(v=val):
            return v

        pf.clock_synced = f
        assert (await _check(client.hub, "clock"))["status"] == want


async def test_mpv_restarts_check(client, fake):  # noqa: F811
    now = time.time()
    assert (await _check(client.hub, "mpv_restarts"))["status"] == "ok"
    fake.restart_times = [now - 7200]
    assert (await _check(client.hub, "mpv_restarts"))["status"] == "ok"
    fake.restart_times = [now - 7200, now - 10]
    assert (await _check(client.hub, "mpv_restarts"))["status"] == "warn"
    fake.restart_times = [now - 30, now - 20, now - 10]
    assert (await _check(client.hub, "mpv_restarts"))["status"] == "fail"


async def test_trigger_recent_check(client, tmp_path, fake):  # noqa: F811
    hub = client.hub
    assert (await _check(hub, "trigger_recent"))["detail"] == "not needed"
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    r = None
    with db.session() as s:
        m = Media(filename="v.mp4", path=str(f), duration_s=5, codec="h264", height=720)
        s.add(m)
        s.commit()
        s.refresh(m)
    r = await client.post(
        "/api/profiles",
        json={"name": "T", "type": "video", "media_id": m.id, "idle_media_id": m.id,
              "playback_mode": "idle_trigger", "base_state": {"on": True}},
    )  # fmt: skip
    await hub.engine.activate(r.json()["id"], autostart=False)
    pf = hub.preflight
    assert (await _check(hub, "trigger_recent"))["status"] == "ok"
    base = pf.now()
    pf.now = lambda: base + 31 * 60
    assert (await _check(hub, "trigger_recent"))["status"] == "warn"
    hub.engine.last_trigger = {"ts": base + 20 * 60, "source": "x", "accepted": True, "message": ""}
    assert (await _check(hub, "trigger_recent"))["status"] == "ok"
    pf.now = lambda: base + 60 * 60
    assert (await _check(hub, "trigger_recent"))["status"] == "warn"


def _capture(hub):
    seen = []
    orig = hub.bus.publish
    hub.bus.publish = lambda t, d=None: (seen.append(t), orig(t, d))[1]
    return seen


async def test_publish_only_on_change(client):  # noqa: F811
    hub = client.hub
    await hub.preflight.stop()  # the real 10 s loop must not add publishes
    seen = _capture(hub)
    hub.preflight._last_sig = None
    await hub.preflight.run()
    await hub.preflight.run()
    assert seen.count("preflight") == 1
    _board(hub, "zz", BoardStatus.offline)
    await hub.preflight.run()
    await hub.preflight.run()
    assert seen.count("preflight") == 2


async def test_detail_only_change_does_not_publish(client):  # noqa: F811
    hub = client.hub
    await hub.preflight.stop()
    b = _board(hub, "aa", lat=120.0)
    await hub.preflight.run()
    seen = _capture(hub)
    b.latency_ms = 150.0
    res = await hub.preflight.run()
    assert seen.count("preflight") == 0
    assert "150" in next(c for c in res["checks"] if c["id"] == "latency")["detail"]
    assert hub.preflight.last == res


async def test_check_exception_becomes_warn(client, monkeypatch):  # noqa: F811
    async def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(client.hub.preflight, "_latency", boom)
    res = await client.hub.preflight.run()
    assert res["checks"][1]["status"] == "warn" and len(res["checks"]) == 10


async def test_supervisor_records_unexpected_exits_only(monkeypatch):
    from spookhub.config import MpvCfg
    from spookhub.events import EventBus
    from spookhub.player.process import MpvPlayer

    p = MpvPlayer(MpvCfg(enabled=True, restart_delay_s=0.0), EventBus())
    old = time.time() - 90000  # older than 24 h: trimmed on the next record
    p.restart_times = [old]
    runs = []

    async def run_once():
        runs.append(1)
        if len(runs) == 1:
            return 1  # crash
        if len(runs) == 2:
            p._restart_requested = True  # requested restart
            return 0
        p._stopping = True
        return 0

    monkeypatch.setattr(p, "_run_once", run_once)
    await asyncio.wait_for(p._supervise(), 5)
    assert len(runs) == 3
    assert len(p.restart_times) == 1 and p.restart_times[0] > old + 80000
    assert p.restart_began is not None


async def test_ntp_synchronized_branches(monkeypatch):
    from spookhub.show import preflight as pf

    class Proc:
        def __init__(self, out=b"yes\n", rc=0, hang=False):
            self.out, self.returncode, self.hang, self.killed = out, None, hang, False
            self._rc = rc

        async def communicate(self):
            if self.hang:
                await asyncio.sleep(30)
            self.returncode = self._rc
            return self.out, b""

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    async def run(proc):
        async def fake_exec(*a, **k):
            if isinstance(proc, Exception):
                raise proc
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        return await pf.ntp_synchronized()

    assert await run(Proc()) is True
    assert await run(Proc(out=b"no\n")) is False
    assert await run(Proc(rc=1)) is None
    assert await run(FileNotFoundError()) is None
    real_timeout = asyncio.timeout
    monkeypatch.setattr(pf.asyncio, "timeout", lambda s: real_timeout(0.05))
    hung = Proc(hang=True)
    assert await run(hung) is None and hung.killed


async def test_wifi_sleep_cached(client):  # noqa: F811
    hub = client.hub
    b = _board(hub, "aa")
    assert (await _check(hub, "wifi_sleep"))["detail"] == "not checked"
    b.wifi_sleep = False
    assert (await _check(hub, "wifi_sleep"))["status"] == "ok"
    b.wifi_sleep = True
    c = await _check(hub, "wifi_sleep")
    assert c["status"] == "warn" and "aa" in c["detail"]


async def test_recommendations_endpoint_caches_wifi_sleep(client, farm):  # noqa: F811
    sim = await farm.add(0)
    mac = (await client.post("/api/boards/add", json={"ip": f"127.0.0.1:{sim.port}"})).json()["mac"]
    await client.post(f"/api/boards/{mac}/adopt", json={"name": "P"})
    b = client.hub.boards.boards[mac]
    assert b.wifi_sleep is None
    r = await client.get(f"/api/boards/{mac}/recommendations")
    assert r.status_code == 200
    ws = next(c for c in r.json() if c["id"] == "wifi_sleep")
    assert b.wifi_sleep is (not ws["ok"])


async def test_mpv_restart_grace(client, fake):  # noqa: F811
    hub = client.hub
    fake.restart_began = time.monotonic()
    c = await _check(hub, "mpv")
    assert c["status"] == "warn" and c["detail"] == "restarting"
    fake.restart_began = time.monotonic() - 10
    assert (await _check(hub, "mpv"))["status"] == "fail"


async def test_media_idle_and_missing_row(client, tmp_path, fake, monkeypatch):  # noqa: F811
    hub = client.hub
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    await _video_profile(client, f, idle=True)
    assert (await _check(hub, "media"))["status"] == "ok"
    with db.session() as s:
        main = s.get(Media, 1)
        idle = Media(filename="i.mp4", path=str(f), codec="vp9", height=720)
        s.add(idle)
        s.commit()
        s.refresh(idle)
        from spookhub.models import Profile

        p = s.get(Profile, hub.engine.active.id)
        p.idle_media_id = idle.id
        s.add(p)
        s.commit()
        assert main is not None
    c = await _check(hub, "media")
    assert c["status"] == "warn" and "i.mp4" in c["detail"]
    # FK enforcement forbids a dangling id in the db, so simulate the lookup result.
    monkeypatch.setattr(hub.preflight, "_active_media", lambda: (True, [("idle media", None)]))
    assert (await _check(hub, "media"))["status"] == "fail"


async def test_hold_white_independent_of_identify(client):  # noqa: F811
    from spookhub.boards.registry import IDENTIFY_STATE

    hub = client.hub
    b = _board(hub, "aa")
    hub.boards.hold_identify()
    assert hub.boards.desired(b) == IDENTIFY_STATE
    assert IDENTIFY_STATE.bri == 255
    b.identify_until = time.monotonic() + 0.01  # user identify ends meanwhile
    await asyncio.sleep(0.05)
    assert hub.boards.desired(b) == IDENTIFY_STATE  # hold survives
    hub.boards.release_identify()
    assert hub.boards.desired(b) is None
    b.identify_until = time.monotonic() + 30
    hub.boards.hold_identify()
    hub.boards.release_identify()
    assert hub.boards.desired(b) == IDENTIFY_STATE  # user identify survives release


async def test_stop_awaits_and_clears_task(client):  # noqa: F811
    pf = client.hub.preflight
    t = pf._task
    await pf.stop()
    assert pf._task is None and t.done()
    await pf.stop()


# ----- test-all and setup runner -----


async def test_failed_wait_still_restores_idle(client, fake):  # noqa: F811
    eng = client.hub.engine
    restored = []

    async def restore():
        restored.append(1)

    eng._restore_idle = restore

    async def start():
        return True

    async def wait():
        raise RuntimeError("wait died")

    await eng.begin_setup(start, wait)
    await wait_for(lambda: not eng.snapshot()["setup_busy"])
    assert restored == [1]

    # A restore that itself fails must not leave setup stuck.
    async def bad():
        raise RuntimeError("restore died")

    eng._restore_idle = bad
    await eng.begin_setup(start, wait)
    await wait_for(lambda: not eng.snapshot()["setup_busy"])


async def test_test_all_409_while_playing(client):  # noqa: F811
    client.hub.engine.playing_main = True
    r = await client.post("/api/preflight/test_all")
    assert r.status_code == 409


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
async def test_test_all_flashes_plays_and_restores(client, fake, monkeypatch):  # noqa: F811
    hub = client.hub
    eng = hub.engine
    b = _board(hub, "aa")
    b.queue = None
    monkeypatch.setattr(hub.boards, "push_all", lambda critical=False: None)
    sound = pattern = None
    orig = eng.run_test_all

    async def fast(s, p, flash_s=2.0, pattern_s=2.0):
        nonlocal sound, pattern
        sound, pattern = s, p
        await orig(s, p, flash_s=0.1, pattern_s=0.1)

    monkeypatch.setattr(eng, "run_test_all", fast)
    r = await client.post("/api/preflight/test_all")
    assert r.status_code == 200
    assert b.hold_white
    assert eng.snapshot()["setup_busy"]
    assert (await client.post("/api/preflight/test_all")).status_code == 409
    await wait_for(lambda: ("load", sound) in fake.calls)
    assert not b.hold_white
    fake.path, fake.eof = sound, True
    await wait_for(lambda: ("load", pattern) in fake.calls)
    await wait_for(lambda: not eng.snapshot()["setup_busy"])
    assert fake.calls[-1] == ("black",)


async def test_test_all_blackout_cancels_and_releases_flash(client, fake, monkeypatch):  # noqa: F811
    hub = client.hub
    eng = hub.engine
    b = _board(hub, "aa")
    monkeypatch.setattr(hub.boards, "push_all", lambda critical=False: None)
    await eng.run_test_all("/s.wav", "/p.png", flash_s=30)
    assert b.hold_white
    await asyncio.wait_for(eng.set_blackout(True), 2)
    assert not b.hold_white and not eng.snapshot()["setup_busy"]


async def test_test_all_works_without_player(client):  # noqa: F811
    hub = client.hub
    b = _board(hub, "aa")
    await hub.engine.run_test_all("/s.wav", "/p.png", flash_s=0.05)
    await wait_for(lambda: not hub.engine.snapshot()["setup_busy"])
    assert not b.hold_white
