"""Derived media assets for the timeline: thumbnail sprite strip and audio waveform peaks.

Stored next to the media files as <media_dir>/<id>.thumbs.jpg/.json and <id>.wave.json, so no
schema change is needed and assets can be regenerated at any time.
"""

from __future__ import annotations

import array
import asyncio
import json
import logging
import os
import weakref
from pathlib import Path
from typing import Any

from spookhub.media import analyze

log = logging.getLogger(__name__)

THUMB_W, THUMB_H = 160, 90
MAX_THUMBS = 100
WAVE_SAMPLE_RATE = 8000
MAX_PEAKS = 20000

# Per event loop (locks are loop-bound; tests run one loop per test).
_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[int, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def paths(media_dir: Path, mid: int) -> dict[str, Path]:
    return {
        "sprite": media_dir / f"{mid}.thumbs.jpg",
        "thumbs": media_dir / f"{mid}.thumbs.json",
        "wave": media_dir / f"{mid}.wave.json",
        "onsets": media_dir / f"{mid}.onsets.json",
    }


async def _run(*args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode(errors="replace").strip()[-500:])
    return out


async def make_thumbs(src: Path, duration: float, sprite: Path, meta: Path) -> dict[str, Any]:
    count = max(1, min(MAX_THUMBS, int(duration) or 1))
    interval = max(duration, 0.1) / count
    vf = (
        f"fps={count / max(duration, 0.1):.6f},"
        f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease,"
        f"pad={THUMB_W}:{THUMB_H}:(ow-iw)/2:(oh-ih)/2,tile={count}x1"
    )
    await _run(
        "ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-frames:v", "1",
        "-q:v", "5", str(sprite),
    )  # fmt: skip
    data = {"count": count, "interval": interval, "tile_w": THUMB_W, "tile_h": THUMB_H}
    _write_json(meta, data)
    return data


def _peaks(raw: bytes, duration: float) -> dict[str, Any]:
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    if not samples:
        return {"rate": 0, "peaks": []}
    n_buckets = max(1, min(MAX_PEAKS, int(max(duration, 1.0) * 50)))
    size = max(1, len(samples) // n_buckets)
    peaks = []
    for i in range(0, len(samples), size):
        chunk = samples[i : i + size]
        peaks.append(round(max(max(chunk), -min(chunk)) / 32768, 3))
    rate = len(peaks) / (len(samples) / WAVE_SAMPLE_RATE)
    top = max(peaks) or 1.0
    # Normalise so quiet soundtracks are still readable.
    return {"rate": rate, "peaks": [round(p / top, 3) for p in peaks]}


async def _has_audio(src: Path) -> bool:
    out = await _run(
        "ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
        "-of", "csv=p=0", str(src),
    )  # fmt: skip
    return bool(out.strip())


async def _decode(src: Path) -> bytes:
    """Mono s16le PCM; b"" only when the file clearly has no audio. Other failures raise."""
    if not await _has_audio(src):
        return b""
    return await _run(
        "ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1",
        "-ar", str(WAVE_SAMPLE_RATE), "-f", "s16le", "-",
    )  # fmt: skip


def _write_json(path: Path, data: Any) -> None:
    """Atomic write so a crash never leaves a truncated file behind."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _valid(path: Path) -> bool:
    """True if the file exists and parses; a corrupt file is deleted and counts as missing."""
    try:
        json.loads(path.read_text())
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return False
    return True


async def make_onsets(src: Path, out: Path, raw: bytes | None = None) -> dict[str, Any]:
    if raw is None:
        raw = await _decode(src)
    data = await asyncio.to_thread(analyze.analyze, raw, WAVE_SAMPLE_RATE)
    _write_json(out, data)
    return data


async def make_wave(
    src: Path, duration: float, out: Path, raw: bytes | None = None
) -> dict[str, Any]:
    if raw is None:
        raw = await _decode(src)
    data = await asyncio.to_thread(_peaks, raw, duration)
    _write_json(out, data)
    return data


def _lock(mid: int) -> asyncio.Lock:
    return _locks.setdefault(asyncio.get_running_loop(), {}).setdefault(mid, asyncio.Lock())


def _prune(mid: int) -> None:
    _locks.get(asyncio.get_running_loop(), {}).pop(mid, None)


async def ensure(media_dir: Path, mid: int, src: Path, duration: float, video: bool = True) -> None:
    """Generate any missing derived assets (idempotent, serialised per media item).

    Audio-only media (video=False) gets waveform and onsets, no thumbnails."""
    async with _lock(mid):
        p = paths(media_dir, mid)
        try:
            if video and not (p["sprite"].exists() and _valid(p["thumbs"])):
                await make_thumbs(src, duration, p["sprite"], p["thumbs"])
        except Exception as e:  # noqa: BLE001
            log.warning("thumbnails for media %s failed: %s", mid, e)
        need_wave, need_onsets = not _valid(p["wave"]), not _valid(p["onsets"])
        raw: bytes | None = None
        if need_wave or need_onsets:
            try:
                raw = await _decode(src)  # decoded once, shared by both analyses
            except Exception as e:  # noqa: BLE001
                log.warning("decoding audio of media %s failed: %s", mid, e)
        if raw is not None and need_wave:
            try:
                await make_wave(src, duration, p["wave"], raw)
            except Exception as e:  # noqa: BLE001
                log.warning("waveform for media %s failed: %s", mid, e)
        if raw is not None and need_onsets:
            try:
                await make_onsets(src, p["onsets"], raw)
            except Exception as e:  # noqa: BLE001
                log.warning("onset analysis for media %s failed: %s", mid, e)
    _prune(mid)


async def remove(media_dir: Path, mid: int) -> None:
    async with _lock(mid):
        for f in paths(media_dir, mid).values():
            f.unlink(missing_ok=True)
    _prune(mid)
