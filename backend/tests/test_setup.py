"""Phase 1 task 2: device discovery, test sound, trigger tracking."""

import asyncio
import shutil

import pytest

from spookhub.config import MpvCfg
from spookhub.events import EventBus
from spookhub.player.devices import drm_outputs, parse_audio_devices
from spookhub.player.ipc import MpvError
from spookhub.player.process import MpvPlayer
from spookhub.show.engine import SetupBusy
from spookhub.show.sounds import ensure_test_sound
from tests.conftest import wait_for
from tests.test_api import client  # noqa: F401  (fixture)


def _conn(root, name, status, modes=(), enabled="disabled"):
    d = root / name
    d.mkdir(parents=True)
    (d / "status").write_text(status + "\n")
    (d / "modes").write_text("".join(m + "\n" for m in modes))
    (d / "enabled").write_text(enabled + "\n")


def test_drm_outputs_fake_sysfs(tmp_path):
    _conn(tmp_path, "card0-HDMI-A-2", "disconnected")
    _conn(tmp_path, "card0-HDMI-A-1", "connected", ["1920x1080", "1280x720"], "enabled")
    (tmp_path / "card0").mkdir()
    (tmp_path / "version").write_text("x")
    outs = drm_outputs(tmp_path)
    assert [o["name"] for o in outs] == ["HDMI-A-1", "HDMI-A-2"]
    assert outs[0] == {
        "card": 0,
        "name": "HDMI-A-1",
        "status": "connected",
        "modes": ["1920x1080", "1280x720"],
        "enabled": True,
    }
    assert outs[1]["modes"] == [] and outs[1]["enabled"] is False
    assert drm_outputs(tmp_path / "missing") == []


def test_parse_audio_devices():
    raw = [
        {"name": "auto", "description": "Autoselect device"},
        {"name": "alsa/hdmi:0"},
        {"description": "no name"},
    ]
    assert parse_audio_devices(raw) == [
        {"name": "auto", "description": "Autoselect device"},
        {"name": "alsa/hdmi:0", "description": "alsa/hdmi:0"},
    ]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
async def test_ensure_test_sound_cached(tmp_path):
    p = await ensure_test_sound(tmp_path)
    assert p.stat().st_size > 100_000
    m = p.stat().st_mtime_ns
    assert await ensure_test_sound(tmp_path) == p and p.stat().st_mtime_ns == m


def test_drm_edge_cases(tmp_path):
    (tmp_path / "card1-DP-2").mkdir()  # no status file at all
    _conn(tmp_path, "card1-DP-10", "disconnected", ["", "800x600", ""])
    _conn(tmp_path, "card0-HDMI-A-1", "connected", ["1280x720"])
    _conn(tmp_path, "card1-Writeback-1", "unknown")
    _conn(tmp_path, "card1-Virtual-1", "connected")
    outs = drm_outputs(tmp_path)
    assert [o["name"] for o in outs] == ["HDMI-A-1", "DP-2", "DP-10"]
    assert [o["card"] for o in outs] == [0, 1, 1]
    assert outs[1]["status"] == "unknown" and outs[1]["modes"] == []
    assert outs[2]["modes"] == ["800x600"]


def test_drm_modes_deduped_keep_first_seen_order(tmp_path):
    modes = ["1920x1080", "1280x720", "1920x1080", "1920x1080", "1280x720", "800x600"]
    _conn(tmp_path, "card0-HDMI-A-1", "connected", modes)
    outs = drm_outputs(tmp_path)
    assert outs[0]["modes"] == ["1920x1080", "1280x720", "800x600"]


class FakePlayer(MpvPlayer):
    """Records calls; finishes the sound when told to."""

    def __init__(self, ok=True):
        super().__init__(MpvCfg(enabled=True), EventBus())
        self.calls = []
        self.ok = ok
        self.stale_after_load = False

    async def load(self, path, start=0.0, loop=False):
        self.calls.append(("load", path))
        if not self.ok:
            raise MpvError("down")
        self.path, self.eof = path, False
        if self.stale_after_load:
            # mpv has not yet reported the new file: old path and eof still show.
            self.path, self.eof = "/old", True

    async def black(self):
        self.calls.append(("black",))
        self.path = None


@pytest.fixture
def fake(client):  # noqa: F811
    f = FakePlayer()
    client.hub.engine.player = f
    return f


async def _mk_profile(client, kind="light"):  # noqa: F811
    body = {"name": "P", "base_state": {"on": True, "bri": 100, "colors": [[1, 2, 3]]}}
    r = await client.post("/api/profiles", json=body)
    return r.json()["id"]


async def _finish(eng, fake, path):
    await wait_for(lambda: fake.path == path)
    fake.eof = True
    await wait_for(lambda: not eng.snapshot()["setup_busy"])


async def test_triggers_reflect_refused_trigger(client):  # noqa: F811
    r = await client.get("/api/setup/triggers")
    assert r.json() == {"last_trigger": None}
    r = await client.post("/api/show/trigger")
    assert r.status_code == 200
    lt = (await client.get("/api/setup/triggers")).json()["last_trigger"]
    assert lt["source"] == "button" and lt["accepted"] is False and lt["message"]
    assert client.hub.engine.snapshot()["last_trigger"] == lt


async def test_trigger_published_and_recorded_even_on_crash(client, monkeypatch):  # noqa: F811
    eng = client.hub.engine
    seen = []
    orig = eng.bus.publish
    monkeypatch.setattr(eng.bus, "publish", lambda t, d=None: (seen.append((t, d)), orig(t, d)))
    await eng.trigger("external")
    assert seen[-1][0] == "trigger_seen" and seen[-1][1]["source"] == "external"

    async def boom(source):
        raise ValueError("x")

    monkeypatch.setattr(eng, "_trigger", boom)
    with pytest.raises(ValueError):
        await eng.trigger("pir")
    assert eng.last_trigger["source"] == "pir" and eng.last_trigger["accepted"] is False


async def test_accepted_trigger_recorded(client, monkeypatch):  # noqa: F811
    eng = client.hub.engine

    async def ok(source):
        return True, "triggered"

    monkeypatch.setattr(eng, "_trigger", ok)
    assert await eng.trigger("external") == (True, "triggered")
    assert eng.last_trigger["accepted"] is True and eng.last_trigger["ts"] > 0


async def test_test_sound_409_while_playing(client):  # noqa: F811
    client.hub.engine.playing_main = True
    r = await client.post("/api/setup/test_sound")
    assert r.status_code == 409


async def test_test_sound_503_when_player_unavailable(client):  # noqa: F811
    # cfg fixture disables mpv: never report ok for a sound that cannot play.
    r = await client.post("/api/setup/test_sound")
    assert r.status_code == 503
    assert not client.hub.engine.snapshot()["setup_busy"]
    client.hub.engine.player = FakePlayer(ok=False)
    r = await client.post("/api/setup/test_sound")
    assert r.status_code == 503


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
async def test_test_sound_plays_then_restores_idle_no_profile(client, fake):  # noqa: F811
    eng = client.hub.engine
    r = await client.post("/api/setup/test_sound")
    assert r.status_code == 200 and r.json() == {"ok": True, "started": True}
    assert eng.snapshot()["setup_busy"]
    path = fake.calls[0][1]
    assert path.endswith("test-sound.wav")
    await _finish(eng, fake, path)
    assert fake.calls[-1] == ("black",)
    assert eng.phase.value == "none"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
async def test_test_sound_restores_light_profile_and_ignores_stale_eof(client, fake):  # noqa: F811
    eng = client.hub.engine
    pid = await _mk_profile(client)
    await eng.activate(pid)
    fake.stale_after_load = True
    r = await client.post("/api/setup/test_sound")
    assert r.status_code == 200
    sound = fake.calls[-1][1]
    await asyncio.sleep(0.3)
    assert fake.path == "/old" and eng.snapshot()["setup_busy"]  # stale eof did not end it
    fake.path, fake.eof = sound, True
    await wait_for(lambda: not eng.snapshot()["setup_busy"])
    assert eng.phase.value == "lights" and eng.active.id == pid


async def test_restore_idle_profile_deleted(client, fake):  # noqa: F811
    eng = client.hub.engine
    pid = await _mk_profile(client)
    await eng.activate(pid)
    await client.delete(f"/api/profiles/{pid}")
    async with eng._lock:
        await eng._restore_idle()
    assert eng.active is None and eng.phase.value == "none"


async def test_restore_idle_video_profile_loads_idle_clip(client, fake, tmp_path):  # noqa: F811
    from spookhub import db
    from spookhub.models import Media

    idle = tmp_path / "idle.mp4"
    idle.write_bytes(b"x")
    with db.session() as s:
        m = Media(filename="i.mp4", path=str(idle), duration_s=5)
        s.add(m)
        s.commit()
        s.refresh(m)
    r = await client.post(
        "/api/profiles",
        json={"name": "V", "type": "video", "media_id": m.id, "idle_media_id": m.id,
              "playback_mode": "idle_trigger", "base_state": {"on": True}},
    )  # fmt: skip
    eng = client.hub.engine
    await eng.activate(r.json()["id"])
    fake.calls.clear()
    async with eng._lock:
        await eng._restore_idle()
    assert fake.calls == [("load", str(idle))]
    assert eng.phase.value == "idle"


async def test_restore_idle_bails_when_something_took_over(client, fake):  # noqa: F811
    eng = client.hub.engine
    for attr, val in [
        ("playing_main", True),
        ("test_pattern", True),
        ("audition", object()),
        ("blackout", True),
    ]:
        fake.calls.clear()
        setattr(eng, attr, val)
        phase = eng.phase
        async with eng._lock:
            await eng._restore_idle()
        assert fake.calls == [] and eng.phase == phase, attr
        setattr(eng, attr, None if attr == "audition" else False)


async def test_failing_restore_does_not_stick_busy_and_blackout_works(client, fake, monkeypatch):  # noqa: F811
    eng = client.hub.engine

    async def boom():
        raise RuntimeError("restore failed")

    monkeypatch.setattr(eng, "_restore_idle", boom)

    async def start():
        return True

    async def wait():
        return None

    await eng.begin_setup(start, wait)
    await wait_for(lambda: not eng.snapshot()["setup_busy"])
    # And a cancel of a task that dies with an error must not raise into blackout.
    ev = asyncio.Event()

    async def wait2():
        ev.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise RuntimeError("dies on cancel") from None

    await eng.begin_setup(start, wait2)
    await ev.wait()
    await asyncio.wait_for(eng.set_blackout(True), 2)
    assert eng.blackout and not eng.snapshot()["setup_busy"]


async def _start_setup(client, fake, monkeypatch):  # noqa: F811
    eng = client.hub.engine
    started = asyncio.Event()

    async def start():
        await fake.load("/s.wav")
        return True

    async def wait():
        started.set()
        await asyncio.sleep(30)

    await eng.begin_setup(start, wait)
    await started.wait()
    return eng


async def test_busy_refuses_other_actions(client, fake, monkeypatch):  # noqa: F811
    eng = await _start_setup(client, fake, monkeypatch)
    assert (await eng.trigger("button")) == (False, "setup test running")
    assert (await client.post("/api/show/test_pattern", json={"on": True})).status_code == 409
    assert (await client.post("/api/show/play", json={"from_s": 0})).status_code == 409
    ok, msg = await eng.audition_update(1, 0.0, True)
    assert not ok and msg == "setup test running"
    with pytest.raises(SetupBusy):
        await eng.play_profile_from(1, 0.0)
    assert (await client.post("/api/setup/test_sound")).status_code == 409
    await eng._cancel_setup()
    assert not eng.snapshot()["setup_busy"]


async def test_setup_refused_during_pattern_audition_blackout(client, fake):  # noqa: F811
    eng = client.hub.engine

    async def start():
        return True

    async def wait():
        return None

    eng.test_pattern = True
    with pytest.raises(SetupBusy):
        await eng.begin_setup(start, wait)
    eng.test_pattern = False
    eng.audition = object()  # type: ignore[assignment]
    with pytest.raises(SetupBusy):
        await eng.begin_setup(start, wait)
    eng.audition = None
    eng.blackout = True
    with pytest.raises(SetupBusy):
        await eng.begin_setup(start, wait)


async def test_blackout_cancels_setup_and_wins(client, fake, monkeypatch):  # noqa: F811
    eng = await _start_setup(client, fake, monkeypatch)
    await asyncio.wait_for(eng.set_blackout(True), 2)
    assert not eng.snapshot()["setup_busy"]
    assert eng.phase.value == "blackout" and eng.blackout
    await asyncio.sleep(0.1)
    assert eng.phase.value == "blackout"
    assert fake.calls[-1] == ("black",)


# ----- final review fixes -----


async def test_lock_on_ends_calibration_and_restores(client, fake):  # noqa: F811
    eng = client.hub.engine
    pid = await _mk_profile(client)
    await eng.activate(pid)
    await eng.set_calibration(True, "/calib.mp4")
    assert eng.calibrating and eng.snapshot()["setup_busy"]
    assert (await client.post("/api/show_lock", json={"on": True})).status_code == 200
    snap = eng.snapshot()
    assert not snap["calibrating"] and not snap["setup_busy"]
    assert eng.phase.value == "lights" and fake.calls[-1] == ("black",)
    assert (await eng._trigger("button"))[1] != "setup test running"


async def test_lock_on_ends_test_sound_and_test_pattern(client, fake):  # noqa: F811
    eng = await _start_setup(client, fake, None)
    await client.post("/api/show_lock", json={"on": True})
    assert not eng.snapshot()["setup_busy"]
    await client.post("/api/show_lock", json={"on": False})
    await eng.set_test_pattern(True, "/grid.png")
    await client.post("/api/show_lock", json={"on": True})
    assert not eng.test_pattern and eng.phase.value == "none"


async def test_lock_allows_turning_setup_off_only(client, fake):  # noqa: F811
    eng = client.hub.engine
    await eng.set_test_pattern(True, "/grid.png")
    await eng.set_calibration(False)
    client.hub.settings.update(show_lock=True)  # bypass the lock-on cleanup
    r = await client.post("/api/show/test_pattern", json={"on": False})
    assert r.status_code == 200 and not eng.test_pattern
    assert (await client.post("/api/show/test_pattern", json={"on": True})).status_code == 423
    assert (await client.post("/api/show/test_pattern", json={"on": 0})).status_code == 423
    r = await client.post("/api/setup/calibration", json={"on": False})
    assert r.status_code == 200
    assert (await client.post("/api/setup/calibration", json={"on": True})).status_code == 423
    r = await client.post("/api/setup/calibration", content=b"{bad", headers={"x": "y"})
    assert r.status_code == 423
    assert (await client.post("/api/preflight/test_all")).status_code == 423
    assert (await client.get("/api/preflight")).status_code == 200


async def test_player_settings_refused_while_playing(client):  # noqa: F811
    eng = client.hub.engine
    eng.playing_main = True
    for key in ("mpv_drm_connector", "mpv_drm_mode", "mpv_vo", "mpv_gpu_context",
                "mpv_audio_device"):  # fmt: skip
        r = await client.put("/api/settings", json={key: "x"})
        assert r.status_code == 409 and "a show is playing" in r.json()["detail"], key
    # Unchanged values and unrelated keys still pass.
    r = await client.put("/api/settings", json={"mpv_vo": None, "sync_offset_ms": 10})
    assert r.status_code == 200
    eng.playing_main = False
    r = await client.put("/api/settings", json={"mpv_drm_connector": "HDMI-A-1"})
    assert r.status_code == 200 and r.json()["mpv_drm_connector"] == "HDMI-A-1"


async def test_player_settings_refused_during_setup(client, fake):  # noqa: F811
    eng = await _start_setup(client, fake, None)
    r = await client.put("/api/settings", json={"mpv_drm_mode": "1280x720"})
    assert r.status_code == 409
    await eng._cancel_setup()


async def test_sync_offset_bounds(client):  # noqa: F811
    for bad in (2001, -2001):
        assert (await client.put("/api/settings", json={"sync_offset_ms": bad})).status_code == 422
    for raw in (b'{"sync_offset_ms": Infinity}', b'{"sync_offset_ms": NaN}'):
        r = await client.put(
            "/api/settings", content=raw, headers={"content-type": "application/json"}
        )
        assert r.status_code == 422
    r = await client.put("/api/settings", json={"sync_offset_ms": -2000})
    assert r.status_code == 200 and r.json()["sync_offset_ms"] == -2000


def test_stored_out_of_range_setting_is_dropped_on_load(client):  # noqa: F811
    from spookhub import db
    from spookhub.models import Setting
    from spookhub.settings_store import KEY, SettingsStore

    with db.session() as s:
        row = s.get(Setting, KEY) or Setting(key=KEY, value={})
        row.value = {**(row.value or {}), "sync_offset_ms": 99999, "master_volume": 42}
        s.add(row)
        s.commit()
    v = SettingsStore().load()
    assert v.sync_offset_ms == 0.0 and v.master_volume == 42


async def test_blackout_clears_test_pattern_and_refuses_it(client, fake):  # noqa: F811
    eng = client.hub.engine
    await eng.set_test_pattern(True, "/grid.png")
    await eng.set_blackout(True)
    assert not eng.test_pattern and eng.phase.value == "blackout"
    assert (await client.post("/api/show/test_pattern", json={"on": True})).status_code == 409
    with pytest.raises(SetupBusy):
        await eng.set_test_pattern(True, "/grid.png")
    await eng.set_blackout(False)
    assert not eng.test_pattern and eng.phase.value == "none"


async def test_engine_stop_cancels_and_awaits_tasks(client, fake):  # noqa: F811
    eng = client.hub.engine
    await _start_setup(client, fake, None)
    setup = eng._setup_task
    stall = asyncio.create_task(asyncio.sleep(30))
    sched = asyncio.create_task(asyncio.sleep(30))
    eng._stall_task, eng._sched = stall, sched
    pub = eng._pub
    await eng.stop()
    assert all(t.done() for t in (setup, stall, sched, pub))
    assert eng._setup_task is None and eng._sched is None and eng._pub is None
    assert eng._stall_task is None
