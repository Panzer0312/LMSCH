"""Optional transcode to H.264 1080p yuv420p + AAC, the format the mini PC decodes in hardware."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from spookhub.models import Media

log = logging.getLogger(__name__)

GOOD_CODECS = {"h264", "hevc"}


def needs_transcode(m: Media) -> bool:
    return m.codec.lower() not in GOOD_CODECS or m.height > 1080 or m.width > 1920


async def transcode(
    src: Path, dest: Path, duration: float, on_progress: Callable[[float], None]
) -> None:
    # yuv420p needs even dimensions; screencasts often have odd ones.
    vf = (
        "scale='min(1920,iw)':'min(1080,ih)'"
        ":force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p"
    )
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y", "-i", str(src),
        "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(dest),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    assert proc.stdout and proc.stderr
    while line := await proc.stdout.readline():
        k, _, v = line.decode(errors="replace").strip().partition("=")
        if k == "out_time_us" and v.isdigit() and duration > 0:
            on_progress(min(0.999, int(v) / 1e6 / duration))
    err = await proc.stderr.read()
    if await proc.wait() != 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError(err.decode(errors="replace").strip()[-400:] or "ffmpeg failed")
