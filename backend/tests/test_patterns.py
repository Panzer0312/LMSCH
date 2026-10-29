import asyncio
import json
import subprocess

import pytest

from spookhub.show import patterns, testpattern
from tests.test_api import client  # noqa: F401

SIZE = (320, 180)
CONTRACT_IDS = [
    "grid", "corners", "circles", "edge_chase", "scan_h", "scan_v",
    "focus", "grey_ramp", "checker", "white", "black", "safe_area",
]  # fmt: skip


def _probe(path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,codec_name,pix_fmt:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    data = json.loads(out)
    return {**data["streams"][0], **data.get("format", {})}


def test_registry_matches_contract():
    assert [p.id for p in patterns.PATTERNS] == CONTRACT_IDS
    kinds = {p.id: p.kind for p in patterns.PATTERNS}
    assert {i for i, k in kinds.items() if k == "video"} == {"edge_chase", "scan_h", "scan_v"}


@pytest.mark.parametrize("pid", CONTRACT_IDS)
async def test_every_pattern_renders_at_requested_size(tmp_path, pid):
    out = await patterns.ensure(tmp_path, pid, SIZE)
    assert out.parent == tmp_path / "patterns"
    info = _probe(out)
    assert (info["width"], info["height"]) == SIZE
    if out.suffix == ".mp4":
        assert info["codec_name"] == "h264" and info["pix_fmt"] == "yuv420p"
        assert 4 <= float(info["duration"]) <= 6
    else:
        assert info["codec_name"] == "png"
    assert not [f for f in out.parent.iterdir() if f.name.startswith(".")]  # no temp left


async def test_cached_and_concurrent_calls_share_one_render(tmp_path, monkeypatch):
    calls = 0
    real = patterns._render

    async def counting(*a):
        nonlocal calls
        calls += 1
        await real(*a)

    monkeypatch.setattr(patterns, "_render", counting)
    a, b = await asyncio.gather(
        patterns.ensure(tmp_path, "checker", SIZE), patterns.ensure(tmp_path, "checker", SIZE)
    )
    assert a == b and calls == 1
    mtime = a.stat().st_mtime_ns
    assert await patterns.ensure(tmp_path, "checker", SIZE) == a
    assert calls == 1 and a.stat().st_mtime_ns == mtime
    # Another size is a separate file.
    assert (await patterns.ensure(tmp_path, "checker", (160, 90))).name == "checker-160x90.png"


async def test_unknown_id_raises_key_error(tmp_path):
    with pytest.raises(KeyError):
        await patterns.ensure(tmp_path, "nope", SIZE)


async def test_failed_render_leaves_nothing(tmp_path, monkeypatch):
    async def boom(pid, size, out):
        out.write_bytes(b"partial")
        raise RuntimeError("ffmpeg died")

    monkeypatch.setattr(patterns, "_render", boom)
    with pytest.raises(RuntimeError):
        await patterns.ensure(tmp_path, "white", SIZE)
    assert list((tmp_path / "patterns").iterdir()) == []


async def test_corners_fallback_without_font(tmp_path, monkeypatch):
    monkeypatch.setattr(patterns, "_font", lambda: None)
    out = await patterns.ensure(tmp_path, "corners", SIZE)
    assert (_probe(out)["width"], _probe(out)["height"]) == SIZE


async def test_testpattern_wrapper_is_grid(tmp_path):
    out = await testpattern.ensure_pattern(tmp_path)
    assert out == tmp_path / "patterns" / "grid-1920x1080.png"
    assert out.exists()


async def test_patterns_route(client):  # noqa: F811
    r = await client.get("/api/display/patterns")
    assert r.status_code == 200
    body = r.json()
    assert [p["id"] for p in body] == CONTRACT_IDS
    assert all(set(p) == {"id", "label", "kind"} and p["label"] for p in body)


async def test_pattern_file_route(client, monkeypatch):  # noqa: F811
    r = await client.get("/api/display/patterns/white/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (await client.get("/api/display/patterns/nope/file")).status_code == 404

    async def boom(pid, size, out):
        raise RuntimeError("ffmpeg died")

    monkeypatch.setattr(patterns, "_render", boom)
    r = await client.get("/api/display/patterns/black/file")
    assert r.status_code == 503
    assert "ffmpeg died" in r.json()["detail"]
