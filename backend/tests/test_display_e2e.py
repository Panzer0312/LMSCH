"""Display geometry end-to-end: real mpv (vo=null) reads back what the hub applied."""

import shutil

import httpx
import pytest
from asgi_lifespan import LifespanManager

from spookhub.config import MpvCfg
from spookhub.main import create_app
from tests.conftest import wait_for
from tests.test_e2e import video  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(
    not (shutil.which("mpv") and shutil.which("ffmpeg")), reason="needs mpv + ffmpeg"
)


async def _props(hub, names):
    ipc = hub.player._ipc
    return {n: await ipc.get_property(n) for n in names}


async def _eventually(hub, name, want, timeout=3.0):
    got = []

    async def check():
        got.append(await hub.player._ipc.get_property(name))
        return got[-1] == want

    import asyncio

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    while not await check():
        if loop.time() - t0 > timeout:
            raise AssertionError(f"{name}: {got[-1]!r} != {want!r}")
        await asyncio.sleep(0.05)


async def test_geometry_reaches_mpv(cfg, video):  # noqa: F811
    cfg.mpv = MpvCfg(
        enabled=True, vo="null", gpu_context=None, ao="null", hwdec="no",
        socket_path=cfg.paths.data_dir / "mpv.sock",
    )  # fmt: skip
    app = create_app(cfg)
    async with LifespanManager(app) as m:
        hub = app.state.hub
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=m.app), base_url="http://hub"
        ) as c:
            await hub.player.ready.wait()
            assert str(hub.player.args()).count("--glsl-shaders=") == 1
            with video.open("rb") as f:
                mid = (await c.post("/api/media", files={"file": ("t.mp4", f)})).json()["id"]
            geom = {
                "zoom": 0.5,
                "scale_x": 1.25,
                "rotate": 90,
                "aspect": 1.5,
                "crop": {"x": 0, "y": 0, "w": 0.5, "h": 1},
                "contrast": 12,
                "corners": [[0.1, 0], [0, 0], [0, 0], [0, -0.05]],
                "flip_h": True,
            }
            dp = (
                await c.post("/api/display/profiles", json={"name": "Scrim", "geometry": geom})
            ).json()["id"]
            await c.put("/api/settings", json={"display_profile_id": dp})
            pid = (
                await c.post(
                    "/api/profiles",
                    json={"name": "V", "type": "video", "media_id": mid, "playback_mode": "loop"},
                )
            ).json()["id"]
            await c.post(f"/api/profiles/{pid}/activate")
            await wait_for(lambda: hub.engine.playing_main)
            p = await _props(
                hub,
                [
                    "video-zoom",
                    "video-scale-x",
                    "video-rotate",
                    "video-aspect-override",
                    "video-crop",
                    "contrast",
                    "glsl-shader-opts",
                ],
            )
            assert p["video-zoom"] == 0.5 and p["video-scale-x"] == 1.25
            assert p["video-rotate"] == 90 and p["contrast"] == 12
            assert p["video-aspect-override"] == pytest.approx(1.5)
            # 320x240 source rotated 90: displayed left half = source bottom half.
            assert p["video-crop"] == "320x120+0+120"
            so = p["glsl-shader-opts"]
            assert so["c0x"] == "0.1" and so["c3y"] == "-0.05" and so["flip_h"] == "1"
            st = (await c.get("/api/display/state")).json()
            assert st["resolved_profile_id"] == dp and st["warp_supported"] is True

            # Setup mode: live edits reach mpv, leaving restores the saved geometry.
            r = await c.post(
                "/api/display/setup", json={"on": True, "source": {"kind": "media", "id": mid}}
            )
            assert r.status_code == 200 and not hub.engine.playing_main
            await c.post("/api/display/live", json={"geometry": {"zoom": -1, "flip_v": True}})
            await _eventually(hub, "video-zoom", -1.0)
            assert (await hub.player._ipc.get_property("glsl-shader-opts"))["flip_v"] == "1"
            assert (await c.post("/api/display/seek", json={"t": 1.0})).status_code == 200
            await c.post("/api/display/setup", json={"on": False})
            await _eventually(hub, "video-zoom", 0.5)

            # A restarted mpv comes back at defaults; the hub re-sends the geometry.
            await hub.player.restart()
            await wait_for(lambda: hub.player.restart_began is None and hub.player.ready.is_set())
            await _eventually(hub, "video-zoom", 0.5, timeout=8.0)

            # Back to identity: mpv defaults again.
            await c.put("/api/settings", json={"display_profile_id": None})
            await _eventually(hub, "video-zoom", 0.0)
            await _eventually(hub, "video-crop", "")
            assert (await hub.player._ipc.get_property("video-aspect-override")) == -1
