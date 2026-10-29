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


async def test_publish_only_on_change(client):  # noqa: F811
    hub = client.hub
    seen = []
    orig = hub.bus.publish
    hub.bus.publish = lambda t, d=None: (seen.append(t), orig(t, d))[1]
    await hub.preflight.run()
    await hub.preflight.run()
    n = seen.count("preflight")
    assert n <= 1
    _board(hub, "zz", BoardStatus.offline)
    await hub.preflight.run()
    await hub.preflight.run()
    assert seen.count("preflight") == n + 1 if n else seen.count("preflight") == 2


async def test_check_exception_becomes_warn(client, monkeypatch):  # noqa: F811
    async def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(client.hub.preflight, "_latency", boom)
    res = await client.hub.preflight.run()
    assert res["checks"][1]["status"] == "warn" and len(res["checks"]) == 10


def test_player_restart_times_recorded():
    from spookhub.config import MpvCfg
    from spookhub.events import EventBus
    from spookhub.player.process import MpvPlayer

    p = MpvPlayer(MpvCfg(enabled=True), EventBus())
    assert p.restart_times == []


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
    assert b.identify_until == float("inf")
    assert eng.snapshot()["setup_busy"]
    assert (await client.post("/api/preflight/test_all")).status_code == 409
    await wait_for(lambda: ("load", sound) in fake.calls)
    assert b.identify_until == 0.0
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
    assert b.identify_until == float("inf")
    await asyncio.wait_for(eng.set_blackout(True), 2)
    assert b.identify_until == 0.0 and not eng.snapshot()["setup_busy"]


async def test_test_all_works_without_player(client):  # noqa: F811
    hub = client.hub
    b = _board(hub, "aa")
    await hub.engine.run_test_all("/s.wav", "/p.png", flash_s=0.05)
    await wait_for(lambda: not hub.engine.snapshot()["setup_busy"])
    assert b.identify_until == 0.0
