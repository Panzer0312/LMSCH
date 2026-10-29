"""M2 backend: audition, capture from board, derived media assets."""

import shutil
import subprocess

import pytest

from tests.conftest import wait_for
from tests.test_api import client  # noqa: F401  (fixture)


async def _setup(client, farm, n=2):  # noqa: F811
    sims = [await farm.add(i) for i in range(n)]
    macs = []
    for s in sims:
        mac = (await client.post("/api/boards/add", json={"ip": f"127.0.0.1:{s.port}"})).json()[
            "mac"
        ]
        await client.post(f"/api/boards/{mac}/adopt", json={"name": f"B{s.index}"})
        macs.append(mac)
    return sims, macs


async def _video_profile(client, cues):  # noqa: F811
    # A light-type row is enough for audition (no video needed); cues need type video,
    # so insert a fake media row through the DB layer.
    from spookhub import db
    from spookhub.models import Media

    with db.session() as s:
        m = Media(filename="x.mp4", path="/nonexistent.mp4", duration_s=10)
        s.add(m)
        s.commit()
        s.refresh(m)
    r = await client.post(
        "/api/profiles",
        json={"name": "T", "type": "video", "media_id": m.id, "playback_mode": "idle_trigger",
              "base_state": {"on": True, "bri": 20}},
    )  # fmt: skip
    pid = r.json()["id"]
    assert (await client.put(f"/api/profiles/{pid}/cues", json=cues)).status_code == 200
    return pid


async def test_audition_fires_cues_and_restores(client, farm):  # noqa: F811
    sims, macs = await _setup(client, farm)
    pid = await _video_profile(
        client,
        [
            {"start_s": 0.3, "state": {"bri": 111}},
            {"start_s": 5.0, "state": {"bri": 222}, "target": {"all": False, "macs": [macs[1]]}},
        ],
    )
    r = await client.post("/api/audition", json={"profile_id": pid, "t": 0.0, "playing": True})
    assert r.status_code == 200
    await wait_for(lambda: all(s.state["bri"] == 111 for s in sims), timeout=2)
    # Seek while paused: state at new position applies immediately.
    await client.post("/api/audition", json={"profile_id": pid, "t": 6.0, "playing": False})
    await wait_for(lambda: sims[1].state["bri"] == 222, timeout=2)
    assert sims[0].state["bri"] == 111
    await client.post("/api/audition/stop")
    await wait_for(lambda: all(s.state["bri"] == 20 for s in sims), timeout=2)


async def test_audition_times_out_without_heartbeat(client, farm):  # noqa: F811
    await _setup(client, farm, 1)
    pid = await _video_profile(client, [])
    await client.post("/api/audition", json={"profile_id": pid, "t": 0.0, "playing": True})
    assert client.hub.engine.audition is not None
    await wait_for(lambda: client.hub.engine.audition is None, timeout=5)


async def test_capture_from_board(client, farm):  # noqa: F811
    sims, macs = await _setup(client, farm, 1)
    sims[0].state["bri"] = 77
    sims[0].state["seg"][0].update({"fx": 9, "pal": 3, "col": [[1, 2, 3], [4, 5, 6], [0, 0, 0]]})
    r = (await client.get(f"/api/boards/{macs[0]}/capture")).json()
    assert r["bri"] == 77 and r["fx"] == 9 and r["pal"] == 3
    assert r["colors"][:2] == [[1, 2, 3], [4, 5, 6]]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
async def test_thumbs_and_waveform(client, tmp_path):  # noqa: F811
    src = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=300", "-t", "4", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-c:a", "aac", str(src)],
        check=True,
    )  # fmt: skip
    with src.open("rb") as f:
        mid = (await client.post("/api/media", files={"file": ("v.mp4", f)})).json()["id"]
    meta = (await client.get(f"/api/media/{mid}/thumbs")).json()
    assert meta["count"] == 4 and meta["tile_w"] == 160
    img = await client.get(f"/api/media/{mid}/thumbs.jpg")
    assert img.headers["content-type"] == "image/jpeg" and len(img.content) > 1000
    wave = (await client.get(f"/api/media/{mid}/waveform")).json()
    assert wave["rate"] > 0 and max(wave["peaks"]) == 1.0
    r = await client.get(f"/api/media/{mid}/file", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100


async def test_audition_muted_board_holds_base(client, farm):  # noqa: F811
    sims, macs = await _setup(client, farm)
    pid = await _video_profile(client, [{"start_s": 0.1, "state": {"bri": 111}}])
    body = {"profile_id": pid, "t": 0.0, "playing": True, "muted": [macs[1]]}
    assert (await client.post("/api/audition", json=body)).status_code == 200
    await wait_for(lambda: sims[0].state["bri"] == 111, timeout=2)
    assert sims[1].state["bri"] == 20
    # Unmuting on a heartbeat releases the cue.
    await client.post("/api/audition", json={**body, "muted": []})
    await wait_for(lambda: sims[1].state["bri"] == 111, timeout=2)
    await client.post("/api/audition/stop")
