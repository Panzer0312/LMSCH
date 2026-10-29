"""Black check: is a video's background dark enough not to light up the scrim?

Samples about one frame per second with ffmpeg `signalstats` and takes the median of YLOW (the
10th-percentile luma), so a mostly-black frame with a bright figure still counts as black.
Result cached as <media_dir>/<id>.black.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import weakref
from pathlib import Path
from typing import Any

from spookhub.subproc import communicate

# Limited-range black is 16/255 ≈ 0.063; anything above this glows visibly on a scrim.
THRESHOLD = 0.07
MAX_SAMPLES = 120
# Decoding a long 4K file on the mini PC can take a while; past this something is wrong.
TIMEOUT_S = 120.0

_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[int, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def cache_path(media_dir: Path, mid: int) -> Path:
    return media_dir / f"{mid}.black.json"


def remove(media_dir: Path, mid: int) -> None:
    cache_path(media_dir, mid).unlink(missing_ok=True)


def _parse(out: str) -> list[float]:
    return [
        float(line.split("=", 1)[1])
        for line in out.splitlines()
        if line.startswith("lavfi.signalstats.YLOW=")
    ]


async def measure(src: Path, duration: float) -> dict[str, Any]:
    fps = 1.0 if duration <= MAX_SAMPLES else MAX_SAMPLES / duration
    # format=yuv420p first: signalstats reports in the source bit depth, 8 bit keeps /255 valid.
    vf = f"fps={fps:.6f},scale=320:-2,format=yuv420p,signalstats,metadata=print:file=-"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-nostdin", "-i", str(src), "-an", "-vf", vf, "-f", "null", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    # TimeoutError propagates (ffmpeg killed); the per-media lock is released by `check`.
    out, err = await communicate(proc, TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="replace").strip()[-300:] or "ffmpeg failed")
    lows = _parse(out.decode(errors="replace"))
    if not lows:
        raise RuntimeError("no video frames could be sampled")
    level = round(statistics.median(lows) / 255, 4)
    ok = level <= THRESHOLD
    msg = (
        "Background is near black."
        if ok
        else f"Background is not black (level {level:.2f}); it will light up the scrim."
    )
    return {"ok": ok, "black_level": level, "sampled": len(lows), "message": msg}


async def check(media_dir: Path, mid: int, src: Path, duration: float) -> dict[str, Any]:
    """Cached result; measured once per media item (serialised so parallel calls share work)."""
    path = cache_path(media_dir, mid)
    lock = _locks.setdefault(asyncio.get_running_loop(), {}).setdefault(mid, asyncio.Lock())
    async with lock:
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
        data = await measure(src, duration)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
        return data
