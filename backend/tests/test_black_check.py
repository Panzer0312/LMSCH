import subprocess
from pathlib import Path

import pytest

from spookhub import db
from spookhub.media import blackcheck
from spookhub.models import Media
from tests.test_api import client  # noqa: F401


def _clip(path: Path, color: str, pix_fmt: str = "yuv420p", codec: str = "libx264") -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=64x36:r=10",
         "-t", "3", "-c:v", codec, "-pix_fmt", pix_fmt, str(path)],
        check=True,
    )  # fmt: skip
    return path


def _media(path: Path, w: int = 64, h: int = 36) -> int:
    with db.session() as s:
        m = Media(filename=path.name, path=str(path), duration_s=3, width=w, height=h)
        s.add(m)
        s.commit()
        s.refresh(m)
        return m.id or 0


async def test_measure_black_and_grey(tmp_path):
    black = await blackcheck.measure(_clip(tmp_path / "b.mp4", "black"), 3)
    assert black["ok"] and black["black_level"] <= blackcheck.THRESHOLD
    assert 2 <= black["sampled"] <= 4
    grey = await blackcheck.measure(_clip(tmp_path / "g.mp4", "0x808080"), 3)
    assert not grey["ok"] and grey["black_level"] > 0.4
    assert "not black" in grey["message"]


async def test_ten_bit_source_normalised(tmp_path):
    src = _clip(tmp_path / "b10.mkv", "0x808080", "yuv420p10le", "ffv1")
    r = await blackcheck.measure(src, 3)
    assert 0.4 < r["black_level"] < 0.6


async def test_measure_rejects_non_video(tmp_path):
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(RuntimeError):
        await blackcheck.measure(bad, 1)


async def test_route_caches_and_404(client, tmp_path):  # noqa: F811
    mid = _media(_clip(tmp_path / "g.mp4", "0x606060"))
    r = await client.get(f"/api/media/{mid}/black_check")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"ok", "black_level", "sampled", "message"} and not body["ok"]
    cache = blackcheck.cache_path(client.hub.cfg.paths.media_dir, mid)
    assert cache.exists()
    # Second call is served from the cache, even after the numbers changed on disk.
    cache.write_text('{"ok": true, "black_level": 0.0, "sampled": 1, "message": "cached"}')
    assert (await client.get(f"/api/media/{mid}/black_check")).json()["message"] == "cached"
    assert (await client.get("/api/media/999/black_check")).status_code == 404
    # Deleting the media drops the cached result.
    assert (await client.delete(f"/api/media/{mid}")).status_code == 200
    assert not cache.exists()


async def test_route_422_on_broken_file(client, tmp_path):  # noqa: F811
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"junk")
    mid = _media(bad)
    assert (await client.get(f"/api/media/{mid}/black_check")).status_code == 422


async def test_media_list_aspect(client, tmp_path):  # noqa: F811
    wide = _media(_clip(tmp_path / "w.mp4", "black"), 1920, 1080)
    unknown = _media(tmp_path / "u.mp4", 0, 0)
    items = {m["id"]: m for m in (await client.get("/api/media")).json()}
    assert items[wide]["aspect"] == pytest.approx(16 / 9, abs=1e-3)
    assert items[unknown]["aspect"] is None
