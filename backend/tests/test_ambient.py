"""Phase 4.3: idle cue track, light idle loop, ambient loop + duck, idle -> main fade."""

import asyncio
import sqlite3

from alembic import command

from spookhub import db
from spookhub.config import MpvCfg
from spookhub.events import EventBus
from spookhub.models import CueIn, DisplayGeometry, Media, ProfileIn
from spookhub.player.process import MpvPlayer
from spookhub.show.engine import Phase, load_active
from tests.conftest import wait_for
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_migrations import _cfg
from tests.test_sound import FakeFx


def _media(tmp_path, name, kind="audio", duration=2.0) -> int:
    f = tmp_path / name
    f.write_bytes(b"x")
    with db.session() as s:
        m = Media(filename=name, path=str(f), duration_s=duration, kind=kind)
        s.add(m)
        s.commit()
        s.refresh(m)
        return m.id or 0


class FakeAmbient(FakeFx):
    def __init__(self):
        super().__init__()
        self.current = None

    async def play(self, path, start=0.0, volume=1.0, fade_in_ms=0, loop=False, owner=None):
        self.current = path
        self.calls.append(("play", path, volume, loop, owner))

    async def stop(self, fade_out_ms=0, owner=None):
        self.current = None
        self.calls.append(("stop", fade_out_ms, owner))

    def ramp(self, volume, ms, then_stop=False):
        self.calls.append(("ramp", round(volume, 3), ms))


class Rec:
    """Board sends of the engine, for one fake adopted board."""

    def __init__(self, eng):
        self.sent = []
        b = eng.boards
        b.adopted_macs = lambda: ["aa"]
        b.latency_comp_ms = lambda mac: 0.0
        b.set_show_state = lambda mac, state, critical=False, segs=None: self.sent.append(
            None if state is None else state.bri
        )


# ---------- migration / models ----------


def test_migration_defaults_and_downgrade(tmp_path):
    dbp = tmp_path / "a.db"
    cfg = _cfg(dbp)
    command.upgrade(cfg, "b2c3e4f5a602")
    con = sqlite3.connect(dbp)
    con.execute(
        "INSERT INTO profile (name, type, sort, selection, base_state, overrides, "
        "playback_mode, cooldown_s) VALUES ('p','video',0,'{}','{}','{}','once',0)"
    )
    con.execute("INSERT INTO cue (profile_id, start_s, target, state) VALUES (1, 0, '{}', '{}')")
    con.commit()
    con.close()
    command.upgrade(cfg, "b3d4f5a6b703")
    con = sqlite3.connect(dbp)
    assert con.execute("SELECT track FROM cue").fetchone()[0] == "main"
    row = con.execute("SELECT ambient, fade_ms, idle_length_s FROM profile").fetchone()
    assert row == (None, 400, None)
    con.close()
    command.downgrade(cfg, "b2c3e4f5a602")
    con = sqlite3.connect(dbp)
    assert "track" not in {r[1] for r in con.execute("PRAGMA table_info(cue)")}
    cols = {r[1] for r in con.execute("PRAGMA table_info(profile)")}
    assert not {"ambient", "fade_ms", "idle_length_s"} & cols
    con.close()


def test_model_defaults():
    assert CueIn.model_validate({"start_s": 0, "state": {}}).track == "main"
    p = ProfileIn(name="x")
    assert p.ambient is None and p.fade_ms == 400 and p.idle_length_s is None


async def test_api_ambient_validation_and_light_idle_cues(client, tmp_path):  # noqa: F811
    c = client
    vid = _media(tmp_path, "v.mp4", kind="video")
    aud = _media(tmp_path, "wind.wav")
    r = await c.post("/api/profiles", json={"name": "L", "ambient": {"media_id": vid}})
    assert r.status_code == 422  # ambient must be audio
    r = await c.post("/api/profiles", json={"name": "L", "ambient": {"media_id": 999}})
    assert r.status_code == 422
    r = await c.post(
        "/api/profiles",
        json={"name": "L", "ambient": {"media_id": aud, "volume": 0.5}, "idle_length_s": 4,
              "fade_ms": 0},
    )  # fmt: skip
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["ambient"] == {"media_id": aud, "volume": 0.5, "duck_to": 0.3}
    assert out["idle_length_s"] == 4 and out["fade_ms"] == 0
    pid = out["id"]
    main = [{"start_s": 0, "state": {"bri": 1}}]
    assert (await c.put(f"/api/profiles/{pid}/cues", json=main)).status_code == 422
    idle = [{"start_s": 1, "state": {"bri": 9}, "track": "idle"}]
    r = await c.put(f"/api/profiles/{pid}/cues", json=idle)
    assert r.status_code == 200 and r.json()["cues"][0]["track"] == "idle"
    assert (await c.delete(f"/api/media/{aud}")).status_code == 409  # ambient uses it
    # Export carries the ambient by filename; import re-links it.
    exp = (await c.get(f"/api/profiles/{pid}/export")).json()
    assert exp["ambient"]["media_filename"] == "wind.wav"
    r = await c.post("/api/profiles/import", json={"data": exp})
    assert r.status_code == 201 and r.json()["ambient"]["media_id"] == aud
    # idle_length_s is a light-profile field.
    r = await c.post(
        "/api/profiles", json={"name": "V", "type": "video", "media_id": vid, "idle_length_s": 3}
    )
    assert r.json()["idle_length_s"] is None


# ---------- idle track ----------


async def _video_idle_profile(c, tmp_path):
    vid = _media(tmp_path, "v.mp4", kind="video", duration=10.0)
    idle = _media(tmp_path, "fog.mp4", kind="video", duration=2.0)
    aud = _media(tmp_path, "creak.wav", duration=0.3)
    r = await c.post(
        "/api/profiles",
        json={"name": "V", "type": "video", "media_id": vid, "idle_media_id": idle,
              "playback_mode": "idle_trigger", "base_state": {"on": True, "bri": 10}},
    )  # fmt: skip
    pid = r.json()["id"]
    cues = [
        {"start_s": 3.0, "state": {"bri": 200}},
        {"start_s": 1.0, "duration_s": 0.5, "state": {"bri": 50}, "track": "idle"},
        {"start_s": 0.5, "kind": "sound", "sound": {"media_id": aud}, "track": "idle"},
    ]
    assert (await c.put(f"/api/profiles/{pid}/cues", json=cues)).status_code == 200
    return pid


async def test_load_active_splits_tracks(client, tmp_path):  # noqa: F811
    pid = await _video_idle_profile(client, tmp_path)
    prof = load_active(pid)
    assert [c.state.bri for c in prof.cues] == [200]
    assert [c.state.bri for c in prof.idle_cues] == [50]
    assert prof.sounds == [] and [s.start for s in prof.idle_sounds] == [0.5]


async def test_idle_track_follows_idle_clip_and_wraps(client, tmp_path):  # noqa: F811
    pid = await _video_idle_profile(client, tmp_path)
    eng = client.hub.engine
    rec = Rec(eng)
    fx = FakeFx()
    eng.fx = fx
    await client.post(f"/api/profiles/{pid}/activate")
    assert eng.phase == Phase.idle and eng._idle_track and not eng._idle_mono
    t = [0.2]
    eng._clock = lambda: (t[0], False)  # stands in for the idle clip's time-pos

    async def at(v):
        t[0] = v
        await asyncio.sleep(0.06)

    await at(0.2)
    await at(0.6)
    await at(1.2)
    assert rec.sent[-1] == 50
    # Played at its offset in the window, then ended on its own (0.3 s clip).
    assert [c[0] for c in fx.calls] == ["play", "stop"] and fx.calls[0][2] == 0.1
    await at(1.6)
    assert rec.sent[-1] == 10  # idle cue window over: base
    fx.calls.clear()
    await at(0.1)  # loop wrap
    await at(0.7)
    await at(1.1)
    assert rec.sent[-1] == 50
    assert [c[0] for c in fx.calls][:1] == ["play"]  # the idle sound fires again after the wrap
    # Trigger: idle track stops, main cues take over.
    assert (await client.post("/api/trigger")).json()["ok"]
    assert eng.playing_main and not eng._idle_track
    await eng.stop_show()
    assert eng.phase == Phase.idle and eng._idle_track


async def test_light_profile_idle_loop(client):  # noqa: F811
    c = client
    r = await c.post(
        "/api/profiles", json={"name": "Breath", "idle_length_s": 0.5, "base_state": {"bri": 10}}
    )
    pid = r.json()["id"]
    cues = [{"start_s": 0.2, "duration_s": 0.15, "state": {"bri": 90}, "track": "idle"}]
    assert (await c.put(f"/api/profiles/{pid}/cues", json=cues)).status_code == 200
    eng = c.hub.engine
    rec = Rec(eng)
    await c.post(f"/api/profiles/{pid}/activate")
    assert eng.phase == Phase.lights and eng._idle_track and eng._idle_mono
    # Breathing: cue and base alternate, loop after loop.
    await wait_for(lambda: rec.sent.count(90) >= 3, timeout=3.0)
    assert rec.sent.count(10) >= 3
    await eng.set_blackout(True)
    assert not eng._idle_track
    await eng.set_blackout(False)
    assert eng._idle_track
    # Without idle_length_s there is no loop.
    body = (await c.get(f"/api/profiles/{pid}")).json()
    await c.put(f"/api/profiles/{pid}", json={**body, "idle_length_s": None})
    assert not eng._idle_track


async def test_scene_runs_idle_profiles_idle_track(client, tmp_path):  # noqa: F811
    c = client
    light = (await c.post("/api/profiles", json={"name": "L", "idle_length_s": 1})).json()["id"]
    cues = [{"start_s": 0.1, "state": {"bri": 70}, "track": "idle"}]
    await c.put(f"/api/profiles/{light}/cues", json=cues)
    item = (await c.post("/api/profiles", json={"name": "I"})).json()["id"]
    scene = {"items": [{"profile_id": item}], "idle_profile_id": light, "light_hold_s": 0.2}
    sid = await c.post("/api/profiles", json={"name": "S", "type": "scene", "scene": scene})
    eng = c.hub.engine
    await c.post(f"/api/profiles/{sid.json()['id']}/activate")
    assert eng.phase == Phase.idle and eng._idle_track and eng._idle_mono
    assert (await c.post("/api/trigger")).json()["ok"]
    assert not eng._idle_track  # the light item owns the boards
    await wait_for(lambda: eng._idle_track, timeout=2.0)


# ---------- ambient ----------


async def test_ambient_duck_restore_and_stop(client, tmp_path):  # noqa: F811
    c = client
    vid = _media(tmp_path, "v.mp4", kind="video", duration=10.0)
    aud = _media(tmp_path, "wind.wav")
    r = await c.post(
        "/api/profiles",
        json={"name": "V", "type": "video", "media_id": vid, "playback_mode": "idle_trigger",
              "ambient": {"media_id": aud, "volume": 0.8, "duck_to": 0.25}, "fade_ms": 300},
    )  # fmt: skip
    pid = r.json()["id"]
    eng = c.hub.engine
    amb = FakeAmbient()
    eng.ambient = amb
    path = load_active(pid).ambient.path
    await c.post(f"/api/profiles/{pid}/activate")
    assert amb.calls == [("play", path, 0.8, True, "ambient")]
    amb.calls.clear()
    assert (await c.post("/api/trigger")).json()["ok"]
    assert amb.calls == [("ramp", 0.2, 300)]  # ducked to duck_to * volume
    amb.calls.clear()
    await eng._finish("ended")
    assert amb.calls == [("ramp", 0.8, 300)]
    amb.calls.clear()
    await c.post(f"/api/profiles/{pid}/activate")  # same ambient keeps playing
    assert amb.calls == []
    await eng.set_blackout(True)
    assert amb.calls == [("stop", 0, "ambient")]
    amb.calls.clear()
    await eng.set_blackout(False)
    assert amb.calls[0][0] == "play"
    amb.calls.clear()
    await eng.deactivate()
    assert amb.calls == [("stop", 300, "ambient")]


async def test_scene_uses_idle_profiles_ambient(client, tmp_path):  # noqa: F811
    c = client
    aud = _media(tmp_path, "wind.wav")
    light = (
        await c.post("/api/profiles", json={"name": "L", "ambient": {"media_id": aud}})
    ).json()["id"]
    scene = {"items": [], "idle_profile_id": light}
    sid = await c.post("/api/profiles", json={"name": "S", "type": "scene", "scene": scene})
    eng = c.hub.engine
    amb = FakeAmbient()
    eng.ambient = amb
    await c.post(f"/api/profiles/{sid.json()['id']}/activate")
    assert [x[0] for x in amb.calls] == ["play"]


# ---------- fade ----------


class LogIpc:
    connected = True

    def __init__(self):
        self.log = []

    async def set_property(self, name, value):
        self.log.append((name, value))

    async def command(self, *args, **kw):
        self.log.append(("cmd", args))


def _enabled_player():
    p = MpvPlayer(MpvCfg(enabled=True), EventBus())
    ipc = LogIpc()
    p._ipc = ipc
    p.ready.set()
    p._volume = 80
    p.geometry = DisplayGeometry(brightness=20)
    return p, ipc


async def test_fade_helpers_restore_geometry_brightness(client):  # noqa: F811
    eng = client.hub.engine
    p, ipc = _enabled_player()
    eng.player = p
    await eng._fade_out(0)
    await eng._fade_in(0)
    assert ipc.log == []  # 0 ms = hard cut, nothing sent
    await eng._fade_out(100)
    bri = [v for n, v in ipc.log if n == "brightness"]
    vol = [v for n, v in ipc.log if n == "volume"]
    assert len(bri) >= 4 and bri == sorted(bri, reverse=True) and bri[-1] == -100
    assert vol[-1] == 0 and p.fade == 0
    ipc.log.clear()
    await p.load("/main.mp4")  # the next file starts black and silent
    assert ("brightness", -100) not in ipc.log  # already sent: diffed away
    assert ipc.log[0] != ("brightness", 20) and ("volume", 0.0) in ipc.log
    ipc.log.clear()
    await eng._fade_in(100)
    bri = [v for n, v in ipc.log if n == "brightness"]
    vol = [v for n, v in ipc.log if n == "volume"]
    assert bri == sorted(bri) and bri[-1] == 20  # the geometry's brightness, not 0
    assert vol[-1] == 80 and p.fade == 1


async def test_trigger_fades_from_idle_clip(client, tmp_path):  # noqa: F811
    c = client
    vid = _media(tmp_path, "v.mp4", kind="video", duration=10.0)
    idle = _media(tmp_path, "fog.mp4", kind="video", duration=2.0)
    r = await c.post(
        "/api/profiles",
        json={"name": "V", "type": "video", "media_id": vid, "idle_media_id": idle,
              "playback_mode": "idle_trigger", "fade_ms": 100},
    )  # fmt: skip
    pid = r.json()["id"]
    eng = c.hub.engine
    await c.post(f"/api/profiles/{pid}/activate")
    p, ipc = _enabled_player()
    p.geometry = DisplayGeometry()
    p.path = "fog.mp4"  # idle clip on screen
    eng.player = p
    try:
        ok, _ = await eng.trigger()
        assert ok
        load_at = next(i for i, e in enumerate(ipc.log) if e[0] == "cmd" and e[1][0] == "loadfile")
        before = [v for n, v in ipc.log[:load_at] if n == "brightness"]
        assert before and before[-1] == -100
        await wait_for(lambda: p.fade == 1.0, timeout=2.0)
        assert [v for n, v in ipc.log if n == "brightness"][-1] == 0
    finally:
        await eng.stop_show()
