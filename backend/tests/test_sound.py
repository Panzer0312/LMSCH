"""Phase 4.2: audio media, effects players, sound cues, soundboard."""

import asyncio
import shutil
import sqlite3
import wave
from pathlib import Path

import pytest
from alembic import command

from spookhub import db
from spookhub.config import MpvCfg
from spookhub.events import EventBus
from spookhub.lock import ALLOWED_EXACT
from spookhub.models import Cue, CueIn, LightState, Media
from spookhub.player.effects import EffectsPlayer
from spookhub.show.engine import load_active
from spookhub.show.resolve import CueSpec, Timeline
from spookhub.show.sound_lane import SoundCue, SoundLane, active
from tests.conftest import wait_for
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_migrations import _cfg

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg"
)


def _wav(path: Path, seconds: float = 0.5) -> Path:
    rate = 8000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            v = 8000 if (i // 20) % 2 else -8000  # 200 Hz square wave
            frames += int(v).to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))
    return path


class FakeFx:
    def __init__(self):
        self.calls = []
        self.current = None
        self.ramp_task = None

    async def play(self, path, start=0.0, volume=1.0, fade_in_ms=0, loop=False, owner=None):
        self.current = path
        self.calls.append(("play", path, round(start, 3), volume, fade_in_ms, loop, owner))

    async def stop(self, fade_out_ms=0, owner=None):
        self.current = None
        self.calls.append(("stop", fade_out_ms, owner))

    def ramp(self, volume, ms, then_stop=False):
        self.calls.append(("ramp", volume, ms))

    async def set_pause(self, p):
        self.calls.append(("pause", p))


async def _settle():
    for _ in range(3):
        await asyncio.sleep(0)


# ---------- migration / models ----------


def test_audio_migration_defaults_and_downgrade(tmp_path):
    dbp = tmp_path / "a.db"
    cfg = _cfg(dbp)
    command.upgrade(cfg, "a9c4e7d2b153")
    con = sqlite3.connect(dbp)
    con.execute(
        "INSERT INTO media (filename, path, size, duration_s, width, height, codec, created_at) "
        "VALUES ('v.mp4','/x',0,1,1,1,'h264','2026-01-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO profile (name, type, sort, selection, base_state, overrides, "
        "playback_mode, cooldown_s) VALUES ('p','video',0,'{}','{}','{}','once',0)"
    )
    con.execute("INSERT INTO cue (profile_id, start_s, target, state) VALUES (1, 0, '{}', '{}')")
    con.commit()
    con.close()
    command.upgrade(cfg, "b1a0d4e5c601")
    con = sqlite3.connect(dbp)
    assert con.execute("SELECT kind FROM media").fetchone()[0] == "video"
    assert con.execute("SELECT kind, sound FROM cue").fetchone() == ("light", None)
    con.close()
    command.downgrade(cfg, "a9c4e7d2b153")
    con = sqlite3.connect(dbp)
    assert "kind" not in {r[1] for r in con.execute("PRAGMA table_info(media)")}
    assert not {"kind", "sound"} & {r[1] for r in con.execute("PRAGMA table_info(cue)")}
    con.close()


def test_model_defaults_and_cue_validation():
    assert Media(filename="a", path="/a").kind == "video"
    c = Cue(profile_id=1, start_s=0)
    assert c.kind == "light" and c.sound is None
    with pytest.raises(ValueError):
        CueIn.model_validate({"start_s": 0})  # light cue needs a state
    with pytest.raises(ValueError):
        CueIn.model_validate({"start_s": 0, "kind": "sound"})  # sound cue needs a sound
    with pytest.raises(ValueError):
        CueIn.model_validate({"start_s": 0, "kind": "sound", "sound": {"media_id": 1, "volume": 2}})
    s = CueIn.model_validate({"start_s": 1, "kind": "sound", "sound": {"media_id": 3}})
    assert s.sound and s.sound.volume == 1.0 and not s.sound.loop
    light = CueIn.model_validate({"start_s": 0, "state": {"bri": 1}, "sound": {"media_id": 3}})
    assert light.sound is None


def test_resolve_ignores_sound_cues():
    base = LightState(bri=5)
    cues = [
        CueSpec(id=1, start=0.0, duration=None, targets=None, state=LightState(bri=10)),
        CueSpec(
            id=2, start=1.0, duration=None, targets=None, state=LightState(bri=99), kind="sound"
        ),
    ]
    tl = Timeline(cues, ["aa"])
    assert tl.state_at("aa", 2.0, base) == (1, base.merged(LightState(bri=10)))
    assert [c.id for c in tl.by_board["aa"]] == [1]


# ---------- effects player ----------


class RecIpc:
    connected = True

    def __init__(self):
        self.props = []
        self.cmds = []

    async def set_property(self, name, value):
        self.props.append((name, value))

    async def command(self, *args, **kw):
        self.cmds.append(args)


def _ready_fx():
    p = EffectsPlayer(MpvCfg(enabled=True), EventBus(), "fx")
    ipc = RecIpc()
    p._ipc = ipc
    p.ready.set()
    return p, ipc


def test_effects_args_and_socket(tmp_path):
    main = MpvCfg(socket_path=tmp_path / "mpv.sock", extra_args=["--drm-connector=X"])
    p = EffectsPlayer(main, EventBus(), "fx")
    p.audio_device_override = "alsa/default"
    a = p.args()
    assert "--no-video" in a and "--audio-device=alsa/default" in a
    assert f"--input-ipc-server={tmp_path / 'mpv-fx.sock'}" in a
    assert "--fullscreen" not in a and "--drm-connector=X" not in a
    amb = EffectsPlayer(main, EventBus(), "ambient")
    assert amb.socket_path != p.socket_path


async def test_effects_volume_caps_and_ramp():
    p, ipc = _ready_fx()
    await p.apply_audio(80, cap=100)
    assert ipc.props[-1] == ("volume", 80)
    await p.play("/s.wav", start=1.5, volume=1.3)
    assert ("volume", 100) in ipc.props  # 80 * 1.3 capped at 100
    assert any(c[0] == "loadfile" and c[1] == "/s.wav" and "start=1.500" in c[4] for c in ipc.cmds)
    ipc.props.clear()
    t = p.ramp(0.5, 100)
    await t
    vols = [v for n, v in ipc.props if n == "volume"]
    assert len(vols) >= 4 and vols[-1] == 40 and vols == sorted(vols, reverse=True)
    # A new ramp cancels the running one.
    slow = p.ramp(1.0, 2000)
    fast = p.ramp(0.0, 20)
    await fast
    assert slow.cancelled() and p.gain == 0.0


async def test_effects_fade_in_and_fade_out_stop():
    p, ipc = _ready_fx()
    await p.play("/s.wav", volume=1.0, fade_in_ms=60)
    assert p.gain == 0.0
    await p.ramp_task
    assert p.gain == 1.0
    await p.stop(fade_out_ms=40)
    await p.ramp_task
    assert ("stop",) in ipc.cmds and p.current is None


async def test_effects_stop_owner_and_disabled():
    p, ipc = _ready_fx()
    await p.play("/board.wav", owner="soundboard")
    await p.stop(owner="cue")  # a cue ending must not cut a soundboard sound
    assert p.current == "/board.wav" and ("stop",) not in ipc.cmds
    await p.stop()
    assert ("stop",) in ipc.cmds
    off = EffectsPlayer(MpvCfg(enabled=False), EventBus(), "fx")
    await off.play("/x.wav")  # no mpv: no-op, no error
    await off.stop()
    await off.shutdown()


def test_effects_track_natural_end():
    p, _ = _ready_fx()
    p.current, p.owner = "/s.wav", "cue"
    p._on_event({"event": "end-file", "reason": "stop"})
    assert p.current == "/s.wav"
    p._on_event({"event": "end-file", "reason": "eof"})
    assert p.current is None


# ---------- sound lane ----------


def _sc(id, start, duration=None, media=2.0, **kw):
    return SoundCue(
        id=id, start=start, duration=duration, path=f"/s{id}.wav", media_duration=media, **kw
    )


def test_active_window_rules():
    cues = [_sc(1, 1.0, duration=2.0), _sc(2, 5.0), _sc(3, 10.0, loop=True)]
    assert active(cues, 0.5) is None
    assert active(cues, 1.0).id == 1
    assert active(cues, 3.0) is None  # timed window over
    assert active(cues, 6.9).id == 2 and active(cues, 7.0) is None  # own end (2 s media)
    assert active(cues, 500.0).id == 3  # looping, no duration: until something else


async def test_lane_fires_once_seeks_to_offset_and_fades_at_end():
    fx, lane = FakeFx(), SoundLane()
    cues = [_sc(1, 1.0, duration=2.0, fade_in_ms=500, fade_out_ms=300)]
    lane.tick(fx, cues, 0.5)
    lane.tick(fx, cues, 1.0)
    lane.tick(fx, cues, 1.1)
    await _settle()
    assert fx.calls == [("play", "/s1.wav", 0.0, 1.0, 500, False, "cue")]
    fx.calls.clear()
    lane.resync()  # seek to 2.5 inside the window: restart at offset, fade already over
    lane.tick(fx, cues, 2.5)
    await _settle()
    assert fx.calls == [("play", "/s1.wav", 1.5, 1.0, 0, False, "cue")]
    fx.calls.clear()
    lane.tick(fx, cues, 3.0)
    await _settle()
    assert fx.calls == [("stop", 300, "cue")]
    fx.calls.clear()
    lane.tick(fx, cues, 1.2)  # loop wrap without resync flag still changes the key
    lane.resync()
    lane.tick(fx, cues, 4.0)  # jump out of the window: cut, no fade
    await _settle()
    assert fx.calls[-1] == ("stop", 0, "cue")


async def test_lane_partial_fade_and_loop_offset():
    fx, lane = FakeFx(), SoundLane()
    lane.tick(fx, [_sc(1, 0.0, fade_in_ms=1000)], 0.4)
    await _settle()
    # Seek 400 ms into a 1 s fade-in: start at 40 % gain and ramp the remaining 600 ms.
    assert fx.calls[0][2:5] == (0.4, 0.4, 0)
    assert fx.calls[1] == ("ramp", 1.0, 600)

    fx, lane = FakeFx(), SoundLane()
    lane.tick(fx, [_sc(2, 10.0, loop=True, media=2.0)], 15.0)
    await _settle()
    assert fx.calls[0][2] == 1.0 and fx.calls[0][5] is True


# ---------- engine + API ----------


def _media(tmp_path, name, kind="audio", duration=2.0):
    f = tmp_path / name
    f.write_bytes(b"x")
    with db.session() as s:
        m = Media(filename=name, path=str(f), duration_s=duration, kind=kind)
        s.add(m)
        s.commit()
        s.refresh(m)
        return m.id


async def _video_profile_with_sound(client, tmp_path):  # noqa: F811
    vid = _media(tmp_path, "v.mp4", kind="video", duration=10.0)
    aud = _media(tmp_path, "boo.wav")
    r = await client.post(
        "/api/profiles",
        json={"name": "S", "type": "video", "media_id": vid, "playback_mode": "idle_trigger"},
    )
    pid = r.json()["id"]
    cues = [
        {"start_s": 1.0, "state": {"bri": 50}},
        {"start_s": 2.0, "duration_s": 1.0, "kind": "sound",
         "sound": {"media_id": aud, "volume": 0.8, "fade_out_ms": 200}},
    ]  # fmt: skip
    r = await client.put(f"/api/profiles/{pid}/cues", json=cues)
    assert r.status_code == 200, r.text
    return pid, aud, r.json()


async def test_sound_cue_roundtrip_and_media_in_use(client, tmp_path):  # noqa: F811
    pid, aud, out = await _video_profile_with_sound(client, tmp_path)
    kinds = [(c["kind"], c["sound"]) for c in out["cues"]]
    assert kinds[0] == ("light", None)
    assert kinds[1][0] == "sound" and kinds[1][1]["media_id"] == aud
    assert kinds[1][1]["volume"] == 0.8
    bad = [{"start_s": 0, "kind": "sound", "sound": {"media_id": 9999}}]
    assert (await client.put(f"/api/profiles/{pid}/cues", json=bad)).status_code == 422
    r = await client.delete(f"/api/media/{aud}")
    assert r.status_code == 409 and "S" in r.json()["detail"]
    # A video profile cannot use an audio file as its video.
    r = await client.post("/api/profiles", json={"name": "X", "type": "video", "media_id": aud})
    assert r.status_code == 422
    prof = load_active(pid)
    assert [c.start for c in prof.cues] == [1.0]  # sound cue kept off the light timeline
    assert len(prof.sounds) == 1 and prof.sounds[0].volume == 0.8


async def test_engine_fires_sound_cues_from_playhead(client, tmp_path):  # noqa: F811
    pid, _, _ = await _video_profile_with_sound(client, tmp_path)
    eng = client.hub.engine
    fx = FakeFx()
    eng.fx = fx
    await client.post(f"/api/profiles/{pid}/activate")
    prof = load_active(pid)
    eng._sched_prof, eng._sched_tl = prof, Timeline(prof.cues, [])
    eng.playing_main = True
    try:
        eng._tick(1.5)
        eng._tick(2.0)
        eng._tick(2.5)
        await _settle()
        assert [c[0] for c in fx.calls] == ["play"] and fx.calls[0][2:4] == (0.0, 0.8)
        fx.calls.clear()
        eng._sound.resync()
        eng._tick(2.6)  # seek inside the window
        await _settle()
        assert fx.calls == [("play", prof.sounds[0].path, 0.6, 0.8, 0, False, "cue")]
        fx.calls.clear()
        eng._tick(3.05)
        await _settle()
        assert fx.calls == [("stop", 200, "cue")]
        # Audition never drives the fx player.
        fx.calls.clear()
        eng._sound.resync()
        eng.playing_main = False
        eng._tick(2.2)
        await _settle()
        assert fx.calls == []
    finally:
        eng.playing_main = False
        eng._sched_prof = eng._sched_tl = None
    fx.calls.clear()
    await eng.stop_show()
    assert ("stop", 0, None) in fx.calls
    fx.calls.clear()
    await eng.set_blackout(True)
    assert ("stop", 0, None) in fx.calls
    await eng.set_blackout(False)
    fx.calls.clear()
    await eng.deactivate()
    assert ("stop", 0, None) in fx.calls


async def test_soundboard_routes_lock_and_blackout(client, tmp_path, monkeypatch):  # noqa: F811
    assert {"/api/sounds/play", "/api/sounds/stop"} <= ALLOWED_EXACT
    aud = _media(tmp_path, "scare.wav")
    fx = FakeFx()
    monkeypatch.setattr(client.hub, "fx", fx)
    assert (await client.post("/api/show_lock", json={"on": True})).status_code == 200
    r = await client.post("/api/sounds/play", json={"media_id": aud, "volume": 0.5})
    assert r.status_code == 200
    assert fx.calls[-1][0] == "play" and fx.calls[-1][3] == 0.5 and fx.calls[-1][6] == "soundboard"
    assert (await client.post("/api/sounds/stop")).status_code == 200
    assert fx.calls[-1] == ("stop", 0, None)
    assert (await client.post("/api/sounds/play", json={"media_id": 999})).status_code == 404
    bad = await client.post("/api/sounds/play", json={"media_id": aud, "volume": 5})
    assert bad.status_code == 422
    await client.post("/api/blackout", json={"on": True})
    r = await client.post("/api/sounds/play", json={"media_id": aud})
    assert r.status_code == 409


async def test_hub_has_effects_players(client):  # noqa: F811
    hub = client.hub
    assert {p.name for p in hub.effects} == {"fx", "ambient"}
    assert hub.engine.fx is hub.fx
    await client.put("/api/settings", json={"master_volume": 60})
    await wait_for(lambda: hub.fx._master == 60 and hub.ambient._master == 60)


@needs_ffmpeg
async def test_audio_upload_probe_and_derived(client, tmp_path):  # noqa: F811
    f = _wav(tmp_path / "creak.wav")
    with f.open("rb") as fh:
        r = await client.post("/api/media", files={"file": ("creak.wav", fh)})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["kind"] == "audio" and m["width"] == 0 and not m["needs_transcode"]
    assert 0.4 < m["duration_s"] < 0.6
    mid = m["id"]
    assert (await client.get(f"/api/media/{mid}/thumbs")).status_code == 404
    assert (await client.get(f"/api/media/{mid}/thumbs.jpg")).status_code == 404
    assert (await client.get(f"/api/media/{mid}/black_check")).status_code == 422
    assert (await client.post(f"/api/media/{mid}/transcode")).status_code == 422
    wave_ = (await client.get(f"/api/media/{mid}/waveform")).json()
    assert wave_["peaks"] and wave_["rate"] > 0
    assert "onsets" in (await client.get(f"/api/media/{mid}/onsets")).json()
    media_dir = client.hub.cfg.paths.media_dir
    assert not (media_dir / f"{mid}.thumbs.jpg").exists()
    listed = (await client.get("/api/media")).json()
    assert listed[0]["kind"] == "audio"
    bad = tmp_path / "x.mp3"
    bad.write_bytes(b"not audio")
    with bad.open("rb") as fh:
        r = await client.post("/api/media", files={"file": ("x.mp3", fh)})
    assert r.status_code == 422


@pytest.mark.skipif(not shutil.which("mpv"), reason="needs mpv")
async def test_effects_player_real_mpv(tmp_path):
    """Real mpv (ao=null): the audio-only instance starts, plays, fades and stops."""
    p = EffectsPlayer(
        MpvCfg(enabled=True, ao="null", socket_path=tmp_path / "mpv.sock"), EventBus(), "fx"
    )
    await p.start()
    try:
        await asyncio.wait_for(p.ready.wait(), 10)
        await p.play(str(_wav(tmp_path / "s.wav", 2.0)), start=0.5, fade_in_ms=40)
        await wait_for(lambda: p.path is not None and p.time_pos is not None)
        await p.stop(fade_out_ms=40)
        await p.ramp_task
        await wait_for(lambda: p.path is None)
        assert p.current is None
    finally:
        await p.shutdown()


async def test_export_import_remaps_sound_cues_by_filename(client, tmp_path):  # noqa: F811
    pid, aud, _ = await _video_profile_with_sound(client, tmp_path)
    data = (await client.get(f"/api/profiles/{pid}/export")).json()
    snd = next(c for c in data["cues"] if c["kind"] == "sound")["sound"]
    assert snd["media_filename"] == "boo.wav"
    # Another hub: same file under another id.
    sub = tmp_path / "other"
    sub.mkdir()
    other = _media(sub, "boo.wav")
    assert other != aud
    r = await client.post("/api/profiles/import", json={"data": data})
    assert r.status_code == 201, r.text
    sounds = [c["sound"] for c in r.json()["cues"] if c["kind"] == "sound"]
    assert [s["media_id"] for s in sounds] == [other] and sounds[0]["volume"] == 0.8

    # Unknown file: that cue is dropped (never the raw id), the rest imports, a warning logs.
    q = client.hub.bus.subscribe()
    snd["media_filename"] = "missing.wav"
    r = await client.post("/api/profiles/import", json={"data": data})
    assert r.status_code == 201, r.text
    assert [c["kind"] for c in r.json()["cues"]] == ["light"]
    warns = []
    while not q.empty():
        m = q.get_nowait()
        if m["type"] == "event" and m["data"]["level"] == "warn":
            warns.append(m["data"]["message"])
    client.hub.bus.unsubscribe(q)
    assert any("missing.wav" in w for w in warns)
    # Legacy export without a filename: same rule.
    del snd["media_filename"]
    r = await client.post("/api/profiles/import", json={"data": data})
    assert [c["kind"] for c in r.json()["cues"]] == ["light"]


async def test_blackout_blacks_picture_before_audio(client, tmp_path):  # noqa: F811
    eng = client.hub.engine
    order = []

    class Fx(FakeFx):
        async def stop(self, fade_out_ms=0, owner=None):
            order.append("fx")
            await super().stop(fade_out_ms, owner)

    eng.fx = Fx()
    orig = eng.player.black

    async def black():
        order.append("black")
        await orig()

    eng.player.black = black
    await eng.set_blackout(True)
    assert order[0] == "black" and "fx" in order
    await eng.set_blackout(False)
