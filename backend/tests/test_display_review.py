"""Phase 3 review fixes: setup refusals, resolution rules, media rotation, ffmpeg timeouts."""

import asyncio
import subprocess
import threading
from pathlib import Path

import pytest

from spookhub import db
from spookhub.media import blackcheck, probe
from spookhub.models import CropRect, DisplayGeometry, Media
from spookhub.player.geometry import crop_px, crop_to_source, to_mpv
from spookhub.show import display, patterns
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_display import _dp, _stub_patterns
from tests.test_setup import FakePlayer


def _media(path="/m.mp4", w=1920, h=1080, dp=None, rotation=None) -> int:
    with db.session() as s:
        m = Media(
            filename=Path(path).name,
            path=path,
            width=w,
            height=h,
            display_profile_id=dp,
            rotation=rotation,
        )
        s.add(m)
        s.commit()
        s.refresh(m)
        return m.id or 0


def _clip(path: Path, rotation: int | None = None) -> Path:
    """320x180 clip; `rotation` is ffmpeg's -display_rotation (counter-clockwise)."""
    plain = path.with_name("plain-" + path.name)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x180:r=10",
         "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(plain)],
        check=True,
    )  # fmt: skip
    if rotation is None:
        return plain
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-display_rotation", str(rotation), "-i", str(plain),
         "-c", "copy", str(path)],
        check=True,
    )  # fmt: skip
    return path


def _spy(client):  # noqa: F811
    seen: list[str] = []
    orig = client.hub.bus.publish
    client.hub.bus.publish = lambda t, d=None: (seen.append(t), orig(t, d))  # type: ignore[method-assign]
    return seen


# ---------- 1: play refused while a test pattern / video setup shows ----------


async def test_play_refused_during_setup_and_test_pattern(client, monkeypatch, tmp_path):  # noqa: F811
    await _stub_patterns(monkeypatch, tmp_path)
    eng = client.hub.engine
    eng.player = FakePlayer()
    assert (await client.post("/api/display/setup", json={"on": True})).status_code == 200
    r = await client.post("/api/show/play", json={"from_s": 0})
    assert r.status_code == 409 and r.json()["detail"] == "setup active — leave video setup first"
    assert eng.display_setup and not eng.playing_main
    pid = (await client.post("/api/profiles", json={"name": "L", "type": "light"})).json()["id"]
    r = await client.post(f"/api/profiles/{pid}/play_from", json={"from_s": 0})
    assert r.status_code == 409 and "setup active" in r.json()["detail"]
    assert eng.display_setup  # not silently ended by activating the profile

    await client.post("/api/display/setup", json={"on": False})
    await eng.set_test_pattern(True, str(tmp_path / "grid.png"))
    r = await client.post("/api/show/play", json={"from_s": 0})
    assert r.status_code == 409 and "setup active" in r.json()["detail"]


# ---------- 2: deleting a display profile re-resolves setup and tells the media list ----------


async def test_delete_profile_in_setup_reresolves(client, monkeypatch, tmp_path):  # noqa: F811
    await _stub_patterns(monkeypatch, tmp_path)
    eng = client.hub.engine
    eng.player = FakePlayer()
    dp = await _dp(client, "Media DP", zoom=0.4)
    mid = _media("/clip.mp4", dp=dp)
    r = await client.post(
        "/api/display/setup", json={"on": True, "source": {"kind": "media", "id": mid}}
    )
    assert r.json()["resolved_profile_id"] == dp and r.json()["geometry"]["zoom"] == 0.4
    seen = _spy(client)
    assert (await client.delete(f"/api/display/profiles/{dp}")).status_code == 200
    assert "media_changed" in seen and "display" in seen
    st = eng.display_state()
    assert st["setup"] and st["resolved_profile_id"] is None and st["geometry"]["zoom"] == 0
    assert eng.player.geometry == DisplayGeometry()

    # Assigning a profile to the media while in setup also applies without re-entering.
    dp2 = await _dp(client, "Second", zoom=0.2)
    await client.patch(f"/api/media/{mid}", json={"display_profile_id": dp2})
    assert eng.display_state()["resolved_profile_id"] == dp2

    # Deleting a profile no media uses does not claim the media list changed.
    other = await _dp(client, "Unused")
    seen.clear()
    await client.delete(f"/api/display/profiles/{other}")
    assert "media_changed" not in seen


# ---------- 3: profile save without display_profile_id leaves it alone ----------


async def test_profile_put_omitting_display_profile_keeps_it(client):  # noqa: F811
    dp = await _dp(client, "Show DP")
    base = {"name": "L", "type": "light"}
    pid = (await client.post("/api/profiles", json={**base, "display_profile_id": dp})).json()["id"]
    r = await client.put(f"/api/profiles/{pid}", json={**base, "name": "L2"})
    assert r.status_code == 200 and r.json()["display_profile_id"] == dp
    r = await client.put(f"/api/profiles/{pid}", json={**base, "display_profile_id": None})
    assert r.json()["display_profile_id"] is None
    # Omitted on create = none.
    r = await client.post("/api/profiles", json={"name": "N", "type": "light"})
    assert r.json()["display_profile_id"] is None


# ---------- 4: setup resolution per source kind ----------


async def test_setup_media_source_ignores_show_profile(client, monkeypatch, tmp_path):  # noqa: F811
    await _stub_patterns(monkeypatch, tmp_path)
    eng = client.hub.engine
    eng.player = FakePlayer()
    g_dp = await _dp(client, "Global", zoom=0.1)
    m_dp = await _dp(client, "Media", zoom=0.2)
    p_dp = await _dp(client, "Profile", zoom=0.3)
    await client.put("/api/settings", json={"display_profile_id": g_dp})
    framed = _media("/framed.mp4", dp=m_dp)
    bare = _media("/bare.mp4")
    pid = (
        await client.post(
            "/api/profiles",
            json={"name": "V", "type": "video", "media_id": framed, "display_profile_id": p_dp},
        )
    ).json()["id"]
    await eng.activate(pid, autostart=False)

    async def setup(src):
        r = await client.post("/api/display/setup", json={"on": True, "source": src})
        assert r.status_code == 200, r.text
        return r.json()["resolved_profile_id"]

    assert await setup({"kind": "media", "id": framed}) == m_dp
    assert await setup({"kind": "media", "id": bare}) == g_dp
    assert await setup({"kind": "pattern", "id": "grid"}) == p_dp
    assert await setup({"kind": "idle"}) == p_dp
    # Live edits survive a source switch (intended: compare the same framing on another source).
    await client.post("/api/display/live", json={"geometry": {"zoom": 0.9}})
    r = await client.post(
        "/api/display/setup", json={"on": True, "source": {"kind": "media", "id": bare}}
    )
    assert r.json()["live"]["zoom"] == 0.9 and r.json()["geometry"]["zoom"] == 0.9


# ---------- 5: leaving /video also clears a plain test pattern ----------


async def test_setup_off_clears_plain_test_pattern(client, tmp_path):  # noqa: F811
    eng = client.hub.engine
    fake = FakePlayer()
    eng.player = fake
    await eng.set_test_pattern(True, str(tmp_path / "grid.png"))
    assert eng.test_pattern and not eng.display_setup
    r = await client.post("/api/display/setup", json={"on": False})
    assert r.status_code == 200
    assert not eng.test_pattern and eng.phase.value == "none" and fake.calls[-1] == ("black",)


# ---------- 6: media rotation ----------


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        ({}, 0),
        ({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": 90}]}, 270),
        ({"side_data_list": [{"rotation": -90}]}, 90),
        ({"side_data_list": [{"rotation": 180}]}, 180),
        ({"side_data_list": [{"rotation": -180}]}, 180),
        ({"tags": {"rotate": "90"}}, 90),
        ({"tags": {"rotate": "270"}}, 270),
        ({"tags": {"rotate": "junk"}}, 0),
    ],
)
def test_rotation_of(stream, expected):
    assert probe.rotation_of(stream) == expected


async def test_probe_reads_rotation(tmp_path):
    info = await probe.probe(_clip(tmp_path / "r.mp4", rotation=90))
    # Coded size stays as stored; mpv turns it 90° counter-clockwise = 270 clockwise.
    assert (info.width, info.height, info.rotation) == (320, 180, 270)
    assert (await probe.probe(_clip(tmp_path / "p.mp4"))).rotation == 0


async def test_media_list_aspect_is_displayed_and_backfill(client, tmp_path):  # noqa: F811
    rotated = _clip(tmp_path / "rot.mp4", rotation=-90)
    plain = _clip(tmp_path / "plain.mp4")
    old_rot = _media(str(rotated), 320, 180)  # pre-migration row: rotation NULL
    old_plain = _media(str(plain), 320, 180)
    gone = _media(str(tmp_path / "missing.mp4"), 320, 180)
    seen = _spy(client)
    assert await probe.backfill_rotation(lambda: seen.append("cb")) == 1
    assert "cb" in seen
    with db.session() as s:
        assert s.get(Media, old_rot).rotation == 90  # type: ignore[union-attr]
        assert s.get(Media, old_plain).rotation == 0  # type: ignore[union-attr]
        assert s.get(Media, gone).rotation == 0  # type: ignore[union-attr]
    assert await probe.backfill_rotation() == 0  # nothing left to probe

    items = {m["id"]: m for m in (await client.get("/api/media")).json()}
    assert items[old_rot]["rotation"] == 90 and items[old_rot]["aspect"] == round(180 / 320, 4)
    assert items[old_plain]["aspect"] == round(320 / 180, 4)
    # Size handed to the player: coded dims plus the file's rotation.
    assert display.media_by_id(old_rot)[1].size == (320, 180, 90)  # type: ignore[index]


def test_to_mpv_crop_accounts_for_file_rotation():
    g = DisplayGeometry(crop={"x": 0.25, "y": 0, "w": 0.75, "h": 0.5})
    # File rotated 270 cw by metadata, no extra video-rotate: crop as for a 270 turn.
    props, _ = to_mpv(g, 320, 180, 270)
    assert props["video-crop"] == "160x135+160+45" and props["video-rotate"] == 0
    # Metadata 270 + video-rotate 90 = upright again: crop as unrotated.
    props, _ = to_mpv(g.model_copy(update={"rotate": 90}), 320, 180, 270)
    assert props["video-crop"] == "240x90+80+0" and props["video-rotate"] == 90


# ---------- 7: crop-then-rotate matches mpv ----------


def _rot_cw(img: list[list[str]], deg: int) -> list[list[str]]:
    for _ in range(deg // 90):
        img = [list(r) for r in zip(*img[::-1], strict=True)]
    return img


def _cut(img: list[list[str]], x: int, y: int, w: int, h: int) -> list[list[str]]:
    return [row[x : x + w] for row in img[y : y + h]]


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
@pytest.mark.parametrize(
    "crop", [(0.25, 0, 0.75, 0.5), (0, 0, 0.5, 0.75), (0.1, 0.2, 0.3, 0.6), (0.5, 0.5, 0.5, 0.5)]
)
def test_crop_matches_mpv_crop_then_rotate(rotate, crop):
    """Model of mpv 0.41 (vo=gpu-next), verified with screenshots of a colour-quadrant clip:
    video-crop cuts the coded frame, then the frame turns clockwise by container rotation plus
    video-rotate. The crop the user drew on the displayed frame must survive that order."""
    sw, sh = 20, 10
    coded = [[f"{x},{y}" for x in range(sw)] for y in range(sh)]
    c = CropRect(x=crop[0], y=crop[1], w=crop[2], h=crop[3])
    spec = crop_px(c, rotate, sw, sh)
    cw, rest = spec.split("x")
    ch, cx, cy = (int(v) for v in rest.split("+"))
    mpv_out = _rot_cw(_cut(coded, cx, cy, int(cw), ch), rotate)
    shown = _rot_cw(coded, rotate)
    dw, dh = len(shown[0]), len(shown)
    want = _cut(shown, round(c.x * dw), round(c.y * dh), round(c.w * dw), round(c.h * dh))
    assert mpv_out == want
    assert crop_to_source(c, rotate)[2:] == ((c.h, c.w) if rotate % 180 else (c.w, c.h))


@pytest.mark.parametrize(
    ("rotate", "expected"),
    # Exact strings whose output was checked in mpv 0.41 on a 320x180 quadrant clip.
    [(0, "240x90+80+0"), (90, "160x135+0+0"), (180, "240x90+0+90"), (270, "160x135+160+45")],
)
def test_crop_strings_verified_in_mpv(rotate, expected):
    assert crop_px(CropRect(x=0.25, y=0, w=0.75, h=0.5), rotate, 320, 180) == expected


# ---------- 8: black check timeout ----------


async def test_black_check_timeout_kills_ffmpeg_and_returns_504(client, monkeypatch, tmp_path):  # noqa: F811
    src = _clip(tmp_path / "b.mp4")
    mid = _media(str(src), 320, 180)
    procs = []
    real = asyncio.create_subprocess_exec

    async def spy(*a, **kw):
        p = await real(*a, **kw)
        procs.append(p)
        return p

    monkeypatch.setattr(blackcheck.asyncio, "create_subprocess_exec", spy)
    monkeypatch.setattr(blackcheck, "TIMEOUT_S", 0.0)
    r = await client.get(f"/api/media/{mid}/black_check")
    assert r.status_code == 504
    assert procs and procs[0].returncode is not None  # killed and reaped
    assert not blackcheck.cache_path(client.hub.cfg.paths.media_dir, mid).exists()
    # Lock released: the next call measures normally.
    monkeypatch.setattr(blackcheck, "TIMEOUT_S", 120.0)
    r = await client.get(f"/api/media/{mid}/black_check")
    assert r.status_code == 200 and "black_level" in r.json()


# ---------- 9/10: pattern rendering off the loop, killed on timeout/cancel ----------


async def test_font_lookup_runs_off_the_event_loop(tmp_path, monkeypatch):
    threads = []

    def font():
        threads.append(threading.get_ident())
        return None

    monkeypatch.setattr(patterns, "_font", font)
    await patterns.ensure(tmp_path, "corners", (320, 180))
    assert threads and threads[0] != threading.get_ident()


async def _spy_ffmpeg(monkeypatch):
    procs = []
    real = asyncio.create_subprocess_exec

    async def spy(*a, **kw):
        p = await real(*a, **kw)
        procs.append(p)
        return p

    monkeypatch.setattr(patterns.asyncio, "create_subprocess_exec", spy)
    return procs


async def test_render_timeout_kills_ffmpeg_and_cleans_up(tmp_path, monkeypatch):
    procs = await _spy_ffmpeg(monkeypatch)
    monkeypatch.setattr(patterns, "RENDER_TIMEOUT_S", 0.0)
    with pytest.raises(RuntimeError, match="timed out"):
        await patterns.ensure(tmp_path, "edge_chase", (1920, 1080))
    assert procs and procs[0].returncode is not None
    assert list((tmp_path / "patterns").iterdir()) == []


async def test_render_cancel_kills_ffmpeg_and_cleans_up(tmp_path, monkeypatch):
    procs = await _spy_ffmpeg(monkeypatch)
    t = asyncio.create_task(patterns.ensure(tmp_path, "edge_chase", (1920, 1080)))
    for _ in range(200):
        if procs:
            break
        await asyncio.sleep(0.01)
    assert procs
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert procs[0].returncode is not None
    assert list((tmp_path / "patterns").iterdir()) == []


# ---------- 11: pattern render failure entering setup is 503 ----------


async def test_setup_pattern_render_failure_is_503(client, monkeypatch):  # noqa: F811
    client.hub.engine.player = FakePlayer()

    async def boom(pid, size, out):
        raise RuntimeError("ffmpeg died")

    monkeypatch.setattr(patterns, "_render", boom)
    r = await client.post(
        "/api/display/setup", json={"on": True, "source": {"kind": "pattern", "id": "white"}}
    )
    assert r.status_code == 503 and "ffmpeg died" in r.json()["detail"]
    assert not client.hub.engine.display_setup
