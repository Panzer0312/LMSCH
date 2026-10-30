"""End-to-end: simulator boards + real mpv (null output) + scheduler.

Checks that cue commands reach the fake boards within ±100 ms of their video timestamp.
"""

import shutil
import statistics
import subprocess

import httpx
import pytest
from asgi_lifespan import LifespanManager

from spookhub.config import MpvCfg
from spookhub.main import create_app
from tests.conftest import wait_for

pytestmark = pytest.mark.skipif(
    not (shutil.which("mpv") and shutil.which("ffmpeg")), reason="needs mpv + ffmpeg"
)

CUES = [1.0, 2.0, 2.5, 3.5]
TOLERANCE_S = 0.1


@pytest.fixture
def video(tmp_path):
    out = tmp_path / "test.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "5", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-c:a", "aac", str(out)],
        check=True,
    )  # fmt: skip
    return out


async def test_cues_hit_boards_on_time(cfg, farm, video):
    cfg.mpv = MpvCfg(
        enabled=True, vo="null", gpu_context=None, ao="null", hwdec="no",
        socket_path=cfg.paths.data_dir / "mpv.sock",
    )  # fmt: skip
    sims = [await farm.add(i) for i in range(3)]
    sims[1].latency_ms = 40  # latency compensation should absorb this
    app = create_app(cfg)
    async with LifespanManager(app) as m:
        hub = app.state.hub
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=m.app), base_url="http://hub"
        ) as c:
            for s in sims:
                mac = (await c.post("/api/boards/add", json={"ip": f"127.0.0.1:{s.port}"})).json()[
                    "mac"
                ]
                await c.post(f"/api/boards/{mac}/adopt", json={"name": f"B{s.index}"})
            await wait_for(lambda: all(b.latency_ms for b in hub.boards.boards.values()))
            with video.open("rb") as f:
                mid = (await c.post("/api/media", files={"file": ("t.mp4", f)})).json()["id"]
            pid = (
                await c.post(
                    "/api/profiles",
                    json={"name": "E2E", "type": "video", "media_id": mid, "playback_mode": "once"},
                )
            ).json()["id"]
            cues = [
                {"start_s": t, "state": {"fx": i + 1, "bri": 10 + i}, "target": {"all": True}}
                for i, t in enumerate(CUES)
            ]
            assert (await c.put(f"/api/profiles/{pid}/cues", json=cues)).status_code == 200
            await hub.player.ready.wait()

            # Sample (monotonic, time-pos) pairs to map video time -> wall clock.
            samples: list[float] = []

            def sample(prop: str) -> None:
                if prop == "time-pos" and hub.player.time_pos is not None:
                    samples.append(hub.player.pos_mono - hub.player.time_pos)

            for s in sims:
                s.log.clear()
            orig = hub.player.on_change
            hub.player.on_change = lambda p: (sample(p), orig(p))
            await c.post(f"/api/profiles/{pid}/activate")
            await wait_for(lambda: hub.engine.phase.value == "idle", timeout=10)

    assert len(samples) > 20
    video_zero = statistics.median(samples)
    errors: list[float] = []
    for s in sims:
        arrivals = {e["body"]["bri"]: e["mono"] for e in s.log if e["body"].get("bri", 0) >= 10}
        for i, t in enumerate(CUES):
            assert 10 + i in arrivals, f"board {s.index} never got cue {i}"
            # Latency stays inside the sim: its log time is when the board 'applies' it.
            err = arrivals[10 + i] - (video_zero + t)
            errors.append(err)
            assert abs(err) < TOLERANCE_S, f"board {s.index} cue {i}: off by {err * 1000:.0f} ms"
    print(f"cue timing error ms: max={max(map(abs, errors)) * 1000:.1f} "
          f"mean={statistics.mean(errors) * 1000:.1f}")  # fmt: skip


async def test_play_from_position_applies_state_at_that_time(cfg, farm, video):
    cfg.mpv = MpvCfg(
        enabled=True, vo="null", gpu_context=None, ao="null", hwdec="no",
        socket_path=cfg.paths.data_dir / "mpv.sock",
    )  # fmt: skip
    sim = await farm.add(0)
    app = create_app(cfg)
    async with LifespanManager(app) as m:
        hub = app.state.hub
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=m.app), base_url="http://hub"
        ) as c:
            mac = (await c.post("/api/boards/add", json={"ip": f"127.0.0.1:{sim.port}"})).json()[
                "mac"
            ]
            await c.post(f"/api/boards/{mac}/adopt", json={"name": "B"})
            with video.open("rb") as f:
                mid = (await c.post("/api/media", files={"file": ("t.mp4", f)})).json()["id"]
            pid = (
                await c.post(
                    "/api/profiles",
                    json={
                        "name": "P",
                        "type": "video",
                        "media_id": mid,
                        "playback_mode": "idle_trigger",
                        "base_state": {"bri": 5},
                    },
                )  # fmt: skip
            ).json()["id"]
            cues = [{"start_s": 1.0, "state": {"bri": 61}}, {"start_s": 4.5, "state": {"bri": 62}}]
            await c.put(f"/api/profiles/{pid}/cues", json=cues)
            await hub.player.ready.wait()
            r = await c.post(f"/api/profiles/{pid}/play_from", json={"from_s": 2.0})
            assert r.status_code == 200
            # Cue 1 started before 2.0 s, so it must be applied right away (no replay needed).
            await wait_for(lambda: sim.state["bri"] == 61, timeout=2)
            pos = hub.player.position()
            assert pos is not None and pos >= 2.0
            await wait_for(lambda: sim.state["bri"] == 62, timeout=4)
            assert hub.engine.active.id == pid


@pytest.fixture
def short_video(tmp_path):
    out = tmp_path / "short.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30",
         "-t", "1.5", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(out)],
        check=True,
    )  # fmt: skip
    return out


async def test_scene_cycle_and_chain(cfg, farm, video, short_video):
    """Scene: idle loop → trigger → item plays once with its cues → back to idle; chain mode."""
    cfg.mpv = MpvCfg(
        enabled=True, vo="null", gpu_context=None, ao="null", hwdec="no",
        socket_path=cfg.paths.data_dir / "mpv.sock",
    )  # fmt: skip
    sim = await farm.add(0)
    app = create_app(cfg)
    async with LifespanManager(app) as m:
        hub = app.state.hub
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=m.app), base_url="http://hub"
        ) as c:
            mac = (await c.post("/api/boards/add", json={"ip": f"127.0.0.1:{sim.port}"})).json()[
                "mac"
            ]
            await c.post(f"/api/boards/{mac}/adopt", json={"name": "B"})

            async def upload(p) -> int:
                with p.open("rb") as f:
                    return (await c.post("/api/media", files={"file": (p.name, f)})).json()["id"]

            idle_mid, scare_mid = await upload(video), await upload(short_video)

            async def profile(**body) -> int:
                r = await c.post("/api/profiles", json=body)
                assert r.status_code == 201, r.text
                return r.json()["id"]

            fog = await profile(
                name="Fog", type="video", media_id=idle_mid, idle_media_id=idle_mid,
                playback_mode="idle_trigger", base_state={"bri": 5},
            )  # fmt: skip
            scare = await profile(name="Scare", type="video", media_id=scare_mid,
                                  base_state={"bri": 20})  # fmt: skip
            await c.put(
                f"/api/profiles/{scare}/cues", json=[{"start_s": 0.5, "state": {"bri": 77}}]
            )
            sid = await profile(
                name="Scene", type="scene",
                scene={"items": [{"profile_id": scare}], "idle_profile_id": fog},
            )  # fmt: skip
            await hub.player.ready.wait()
            eng = hub.engine

            await c.post(f"/api/profiles/{sid}/activate")
            await wait_for(lambda: hub.player.path == str(hub.engine.active.idle_path), timeout=5)
            await wait_for(lambda: sim.state["bri"] == 5, timeout=2)
            assert eng.phase.value == "idle"

            assert (await c.post("/api/show/trigger")).json()["ok"]
            assert eng.active.id == scare
            await wait_for(lambda: sim.state["bri"] == 77, timeout=3)  # item's cue fired
            await wait_for(lambda: eng.phase.value == "idle", timeout=5)  # item ended
            assert eng.active.id == sid and not eng.playing_main
            await wait_for(lambda: sim.state["bri"] == 5, timeout=2)  # idle base back
            await wait_for(lambda: hub.player.path == eng.active.idle_path, timeout=5)
            assert (await c.get("/api/scene")).json()["history"] == [scare]

            # Chain: items follow each other without a trigger.
            body = (await c.get(f"/api/profiles/{sid}")).json()
            body["scene"]["chain"] = True
            await c.put(f"/api/profiles/{sid}", json=body)
            await c.post(f"/api/profiles/{sid}/activate")
            await wait_for(lambda: len(eng.scene_state()["history"]) >= 3, timeout=10)
            await c.post("/api/show/stop")
            await wait_for(lambda: eng.phase.value == "idle", timeout=3)
