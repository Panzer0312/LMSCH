"""Phase 1 task 2: device discovery, test sound, trigger tracking."""

import shutil

import pytest

from spookhub.player.devices import drm_outputs, parse_audio_devices
from spookhub.show.sounds import ensure_test_sound
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


async def test_triggers_reflect_refused_trigger(client):  # noqa: F811
    r = await client.get("/api/setup/triggers")
    assert r.json() == {"last_trigger": None}
    r = await client.post("/api/show/trigger")
    assert r.status_code == 200
    lt = (await client.get("/api/setup/triggers")).json()["last_trigger"]
    assert lt["source"] == "button" and lt["accepted"] is False and lt["message"]
    assert client.hub.engine.snapshot()["last_trigger"] == lt


async def test_test_sound_409_while_playing(client):  # noqa: F811
    client.hub.engine.playing_main = True
    r = await client.post("/api/setup/test_sound")
    assert r.status_code == 409


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
async def test_test_sound_ok_when_idle(client):  # noqa: F811
    r = await client.post("/api/setup/test_sound")
    assert r.status_code == 200
    assert (await client.get("/api/setup/audio_devices")).json() == []
