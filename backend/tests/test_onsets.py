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
    res = analyze.analyze(raw, SR)
    assert time.perf_counter() - t0 < 6
    assert len(res["onsets"]) == 600


def test_ten_seconds_of_silence():
    res = analyze.analyze(bytes(SR * 10 * 2), SR)
    assert res["onsets"] == [] and res["bpm"] is None


def test_irregular_clicks_have_no_bpm():
    rnd = random.Random(7)
    times, t = [], 0.3
    while t < 28:
        times.append(t)
        t += rnd.uniform(0.15, 1.9)
    assert analyze.analyze(_clicks(times, 30.0), SR)["bpm"] is None


def test_clicks_50ms_apart_merge_into_one():
    res = analyze.analyze(_clicks([1.0, 1.05], 3.0), SR)
    assert len(res["onsets"]) == 1


def test_click_at_time_zero_detected():
    res = analyze.analyze(_clicks([0.0, 1.0], 3.0), SR)
    assert [o["t"] for o in res["onsets"]][0] <= 0.02 and len(res["onsets"]) == 2


def test_very_quiet_click_next_to_loud_one_is_dropped():
    # Documented behaviour: the global floor (FLOOR_FRAC of the loudest onset) hides it.
    loud = array.array("h", _clicks([1.0], 3.0))
    quiet = array.array("h", [0] * len(loud))
    for k in range(40):
        quiet[2 * SR + k] = 1 if k % 2 else -1  # log scale: only ~7 % of the loud strength
    mixed = array.array("h", (a + b for a, b in zip(loud, quiet, strict=True)))
    res = analyze.analyze(mixed.tobytes(), SR)
    assert [round(o["t"]) for o in res["onsets"]] == [1]


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
    assert data["rate"] == 100 and len(data["onsets"]) >= 11
    assert data["bpm"] is not None and abs(data["bpm"] - 120) <= 2
