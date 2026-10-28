"""Phase 1 task 1: mpv overrides, audio controls, caps, version."""

import asyncio
import json

from spookhub.config import MpvCfg
from spookhub.events import EventBus
from spookhub.hub import load_version
from spookhub.player.process import MpvPlayer
from tests.conftest import wait_for
from tests.test_api import client  # noqa: F401  (fixture)


def test_args_contain_overrides_before_extra_args():
    p = MpvPlayer(MpvCfg(extra_args=["--foo"]), EventBus())
    base = p.args()
    assert not any(a.startswith(("--drm-", "--audio-device")) for a in base)
    p.drm_connector_override = "HDMI-A-1"
    p.drm_mode_override = "1920x1080@60"
    p.audio_device_override = "alsa/hdmi:0"
    a = p.args()
    assert "--drm-connector=HDMI-A-1" in a
    assert "--drm-mode=1920x1080@60" in a
    assert "--audio-device=alsa/hdmi:0" in a
    assert a[-1] == "--foo"


class FakeIpc:
    connected = True

    def __init__(self):
        self.props = []

    async def set_property(self, name, value):
        self.props.append((name, value))

    async def command(self, *args, **kw):
        return None


async def test_player_remembers_and_reapplies_audio(cfg):
    p = MpvPlayer(MpvCfg(enabled=True), EventBus())
    await p.apply_audio(80, 0.2)  # not running: stored only
    ipc = FakeIpc()
    p._ipc = ipc
    p.ready.set()
    await p.load("x.mp4")
    assert ("volume", 80) in ipc.props and ("audio-delay", 0.2) in ipc.props
    # Restart path: remembered state comes back via args.
    assert "--volume=80" in p.args() and "--audio-delay=0.2" in p.args()
    ipc.props.clear()
    await p.apply_audio(50, -1.0)
    assert ipc.props == [("volume", 50), ("audio-delay", -1.0)]
    ipc.props.clear()
    await p.set_audio_device("alsa/x")
    assert ipc.props == [("audio-device", "alsa/x")]
    assert p.audio_device_override == "alsa/x"


async def test_settings_changes_drive_player(client):  # noqa: F811
    hub = client.hub
    calls = []

    async def restart():
        calls.append(("restart",))

    async def apply_audio(v, d):
        calls.append(("audio", v, d))

    async def set_dev(n):
        calls.append(("dev", n))

    hub.player.restart = restart
    hub.player.apply_audio = apply_audio
    hub.player.set_audio_device = set_dev

    await client.put("/api/settings", json={"mpv_drm_connector": "HDMI-A-2"})
    await wait_for(lambda: ("restart",) in calls)
    assert hub.player.drm_connector_override == "HDMI-A-2"
    await client.put("/api/settings", json={"mpv_audio_device": "alsa/y"})
    await wait_for(lambda: ("dev", "alsa/y") in calls)
    assert calls.count(("restart",)) == 1
    await client.put("/api/settings", json={"master_volume": 70})
    await wait_for(lambda: ("audio", 70, 0.0) in calls)
    await client.put("/api/settings", json={"audio_delay_s": 0.5})
    await wait_for(lambda: ("audio", 70, 0.5) in calls)
    await client.put("/api/settings", json={"volume_cap": 40})
    await wait_for(lambda: ("audio", 40, 0.5) in calls)


async def test_caps_clamp_and_show_lock_ignored(client):  # noqa: F811
    r = await client.put("/api/settings", json={"volume_cap": 60, "master_volume": 100})
    assert r.json()["master_volume"] == 60
    r = await client.put("/api/settings", json={"master_volume": 90})
    assert r.json()["master_volume"] == 60
    r = await client.put("/api/settings", json={"brightness_cap": 0.4, "master_brightness": 1})
    assert r.json()["master_brightness"] == 0.4
    assert client.hub.boards.master() == 0.4
    r = await client.put("/api/settings", json={"show_lock": True})
    assert r.json()["show_lock"] is False


def test_version_fallback_and_file(tmp_path):
    assert load_version(tmp_path / "missing.json") == {"commit": "dev"}
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert load_version(bad) == {"commit": "dev"}
    good = tmp_path / "version.json"
    good.write_text(json.dumps({"commit": "abc", "installed_at": "now"}))
    assert load_version(good) == {"commit": "abc", "installed_at": "now"}


async def test_state_includes_version(client):  # noqa: F811
    v = (await client.get("/api/state")).json()["version"]
    assert "commit" in v


async def test_rejected_audio_device_reverts():
    from spookhub.player.ipc import MpvError

    class BadIpc(FakeIpc):
        async def set_property(self, name, value):
            raise MpvError("nope")

    bus = EventBus()
    p = MpvPlayer(MpvCfg(enabled=True), bus)
    p.audio_device_override = "good"
    p._ipc = BadIpc()
    p.ready.set()
    await p.set_audio_device("bad")
    assert p.audio_device_override == "good"
    assert not any(a.startswith("--audio-device=bad") for a in p.args())
    assert any("rejected" in e["message"] for e in bus.log)


async def test_hub_listener_calls(client):  # noqa: F811
    hub = client.hub
    calls = []

    async def restart():
        calls.append("restart")

    async def apply_audio(v, d):
        calls.append(("audio", v, d))

    async def set_dev(n):
        calls.append(("dev", n))

    hub.player.restart = restart
    hub.player.apply_audio = apply_audio
    hub.player.set_audio_device = set_dev

    hub.settings.update(master_volume=70, volume_cap=40)
    await wait_for(lambda: ("audio", 40, 0.0) in calls)
    calls.clear()
    hub.settings.update(master_volume=30)
    await wait_for(lambda: ("audio", 30, 0.0) in calls)
    hub.settings.update(mpv_audio_device="alsa/z")
    await wait_for(lambda: ("dev", "alsa/z") in calls)
    await asyncio.sleep(0.1)
    assert "restart" not in calls
    hub.settings.update(mpv_vo="gpu", mpv_drm_connector="HDMI-A-1")
    await wait_for(lambda: "restart" in calls)
    await asyncio.sleep(0.1)
    assert calls.count("restart") == 1


async def test_non_finite_settings_rejected(client):  # noqa: F811
    for bad in (True, "x", None):
        r = await client.put("/api/settings", json={"master_volume": bad})
        assert r.status_code == 422
    r = await client.put(
        "/api/settings",
        content='{"volume_cap": NaN}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422


async def test_audio_applied_during_startup_is_sent_when_ready():
    p = MpvPlayer(MpvCfg(enabled=True), EventBus())
    await p.apply_audio(90, 0.0)
    p._launched_audio = (p._volume, p._audio_delay_s)  # as _run_once does at spawn
    ipc = FakeIpc()
    await p._sync_audio_after_launch(ipc)
    assert ipc.props == []  # nothing changed since launch: no extra IPC
    await p.apply_audio(55, 0.3)  # while not ready: stored only
    assert ipc.props == []
    await p._sync_audio_after_launch(ipc)
    assert ipc.props == [("volume", 55), ("audio-delay", 0.3)]


async def test_huge_int_setting_is_422(client):  # noqa: F811
    r = await client.put(
        "/api/settings",
        content='{"master_volume": ' + "9" * 400 + "}",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
