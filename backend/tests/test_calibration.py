"""Phase 1 task 4: per-board offset and sync calibration."""

import asyncio
import shutil
import subprocess

import pytest

from spookhub.models import BoardSelection, LightState, PlaybackMode, ProfileType
from spookhub.show.calibration import calibration_cues, ensure_calibration_clip
from spookhub.show.engine import ActiveProfile
from spookhub.show.resolve import Timeline
from tests.conftest import wait_for
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_setup import FakePlayer, _mk_profile


async def _adopt(client, farm, n):  # noqa: F811
    macs = []
    for i in range(n):
        sim = await farm.add(i)
        r = await client.post("/api/boards/add", json={"ip": f"127.0.0.1:{sim.port}"})
        mac = r.json()["mac"]
        await client.post(f"/api/boards/{mac}/adopt", json={"name": f"B{i}"})
        macs.append(mac)
    return macs


def test_calibration_cues():
    cues = calibration_cues()
    assert [c.start for c in cues] == [float(t) for t in range(0, 20, 2)]
    for c in cues:
        assert c.duration == 0.1 and c.targets is None
        assert c.state.on and c.state.bri == 255 and c.state.colors == [(255, 255, 255)]
    assert len({c.id for c in cues}) == 10


async def test_offset_patch_validated_persisted_and_in_snapshot(client, farm):  # noqa: F811
    (mac,) = await _adopt(client, farm, 1)
    r = await client.patch(f"/api/boards/{mac}", json={"offset_ms": -120.5})
    assert r.status_code == 200 and r.json()["offset_ms"] == -120.5
    assert r.json()["name"] == "B0"  # name untouched
    for bad in (501, -500.1):
        assert (
            await client.patch(f"/api/boards/{mac}", json={"offset_ms": bad})
        ).status_code == 422
    boards = client.hub.boards
    boards.boards.clear()
    boards.load_from_db()
    assert boards.boards[mac].offset_ms == -120.5


async def test_latency_comp_adds_offset_cap_on_measured_only(client, farm):  # noqa: F811
    (mac,) = await _adopt(client, farm, 1)
    bm = client.hub.boards
    b = bm.boards[mac]
    b.latency_ms = 1000.0  # half = 500, above the cap
    cap = bm.timing.latency_comp_cap_ms
    b.offset_ms = 0.0
    assert bm.latency_comp_ms(mac) == cap
    b.offset_ms = 200.0
    assert bm.latency_comp_ms(mac) == cap + 200.0
    b.latency_ms = None
    b.offset_ms = -50.0
    assert bm.latency_comp_ms(mac) == -50.0
    assert bm.latency_comp_ms("nope") == 0.0


async def test_board_offset_shifts_scheduler_fire_time(client, farm):  # noqa: F811
    m0, m1 = await _adopt(client, farm, 2)
    eng = client.hub.engine
    eng.boards.boards[m0].latency_ms = None
    eng.boards.boards[m1].latency_ms = None
    await client.patch(f"/api/boards/{m1}", json={"offset_ms": 100})
    prof = ActiveProfile(
        id=-1, name="c", type=ProfileType.video, selection=BoardSelection(all=True),
        base=LightState(on=False), overrides={}, mode=PlaybackMode.loop, cooldown_s=0,
        media_path=None, media_duration=20, idle_path=None, cues=calibration_cues(),
    )  # fmt: skip
    eng._sched_prof, eng._sched_tl = prof, Timeline(prof.cues, eng.boards.adopted_macs())
    eng._last_cue = {m0: None, m1: None}
    sent = []
    eng.boards.set_show_state = lambda mac, st, critical: sent.append((mac, st.on))  # type: ignore[method-assign]
    eng.settings.update(sync_offset_ms=0)
    eng._tick(1.95)  # board 1 looks 100 ms ahead: already inside the 2.0 s flash
    assert (m1, True) in sent and (m0, True) not in sent
    eng._tick(2.0)
    assert (m0, True) in sent


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
async def test_clip_rendered_and_cached(tmp_path):
    p = await ensure_calibration_clip(tmp_path)
    m = p.stat().st_mtime_ns
    assert await ensure_calibration_clip(tmp_path) == p and p.stat().st_mtime_ns == m
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height,duration",
         "-of", "csv=p=0", str(p)],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    assert out[0].startswith("h264,1920,1080") and out[1].startswith("aac")
    assert not list(tmp_path.glob("*.tmp*"))


async def test_calibration_409_while_playing_and_503_without_player(client):  # noqa: F811
    eng = client.hub.engine
    eng.playing_main = True
    r = await client.post("/api/setup/calibration", json={"on": True})
    assert r.status_code == 409
    eng.playing_main = False


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
async def test_calibration_503_when_player_unavailable(client):  # noqa: F811
    r = await client.post("/api/setup/calibration", json={"on": True})  # mpv disabled
    assert r.status_code == 503
    assert not client.hub.engine.calibrating and not client.hub.engine.snapshot()["setup_busy"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
async def test_calibration_on_off_restores_light_profile(client, farm):  # noqa: F811
    (mac,) = await _adopt(client, farm, 1)
    sim = farm.boards[0]
    eng = client.hub.engine
    eng.player = FakePlayer()
    pid = await _mk_profile(client)
    await eng.activate(pid)
    await wait_for(lambda: sim.state["bri"] == 100)
    r = await client.post("/api/setup/calibration", json={"on": True})
    assert r.status_code == 200 and r.json()["calibrating"]
    snap = eng.snapshot()
    assert snap["calibrating"] and snap["setup_busy"] and eng._sched is not None
    assert eng.player.calls[-1][1].endswith("calibration-v1.mp4")
    # Other actions refuse while calibrating; a repeated "on" is harmless.
    assert (await client.post("/api/setup/test_sound")).status_code == 409
    assert (await client.post("/api/setup/calibration", json={"on": True})).status_code == 200
    await client.post("/api/setup/calibration", json={"on": False})
    await wait_for(lambda: not eng.snapshot()["setup_busy"])
    snap = eng.snapshot()
    assert not snap["calibrating"] and snap["phase"] == "lights"
    assert eng._sched is None or eng._sched.done()
    await wait_for(lambda: sim.state["bri"] == 100 and sim.state["on"])
    assert eng.player.calls[-1] == ("black",)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
async def test_blackout_cancels_calibration(client, farm):  # noqa: F811
    await _adopt(client, farm, 1)
    eng = client.hub.engine
    eng.player = FakePlayer()
    await client.post("/api/setup/calibration", json={"on": True})
    assert eng.calibrating
    await asyncio.wait_for(eng.set_blackout(True), 2)
    assert not eng.calibrating and not eng.snapshot()["setup_busy"]
    assert eng._sched is None and eng.phase.value == "blackout"
    # Off after the fact is a no-op.
    assert (await client.post("/api/setup/calibration", json={"on": False})).status_code == 200
