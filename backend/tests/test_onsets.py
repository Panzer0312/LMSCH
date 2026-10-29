from __future__ import annotations

import array
import random
import shutil
import subprocess
import time

import pytest

from spookhub.media import analyze

from .test_timeline_api import client  # noqa: F401

SR = 8000


def _clicks(times: list[float], total: float) -> bytes:
    rnd = random.Random(1)
    buf = array.array("h", [0] * int(total * SR))
    for t in times:
        i = int(t * SR)
        for k in range(40):  # 5 ms burst
            buf[i + k] = rnd.choice((-20000, 20000))
    return buf.tobytes()


def test_five_clicks_found_within_20ms():
    times = [0.5, 1.3, 2.2, 3.0, 4.1]
    res = analyze.analyze(_clicks(times, 5.0), SR)
    got = [o["t"] for o in res["onsets"]]
    assert len(got) == 5
    assert all(abs(g - t) <= 0.02 for g, t in zip(got, times, strict=True))
    assert max(o["strength"] for o in res["onsets"]) == 1.0


def test_silence_has_no_onsets():
    res = analyze.analyze(bytes(SR * 4), SR)
    assert res["onsets"] == [] and res["bpm"] is None


def test_120_bpm_click_train():
    res = analyze.analyze(_clicks([0.25 + 0.5 * k for k in range(40)], 21.0), SR)
    assert res["bpm"] is not None and abs(res["bpm"] - 120) <= 2


def test_long_clip_is_fast():
    raw = _clicks([0.25 + 0.5 * k for k in range(600)], 300.0)
    t0 = time.perf_counter()
    analyze.analyze(raw, SR)
    assert time.perf_counter() - t0 < 6


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
async def test_onsets_route(client, tmp_path):  # noqa: F811
    src = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
         "-f", "lavfi", "-i",
         "aevalsrc='if(lt(mod(t,0.5),0.02),0.8*sin(2*PI*1000*t),0)':s=8000:d=6",
         "-t", "6", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", str(src)],
        check=True,
    )  # fmt: skip
    with src.open("rb") as f:
        r = await client.post("/api/media", files={"file": ("v.mp4", f)})
    mid = r.json()["id"]
    data = (await client.get(f"/api/media/{mid}/onsets")).json()
    assert data["rate"] == 100 and len(data["onsets"]) >= 10
    assert data["bpm"] is not None and abs(data["bpm"] - 120) <= 2
