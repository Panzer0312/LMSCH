"""ffprobe wrapper."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path


class ProbeError(Exception):
    pass


@dataclass
class ProbeResult:
    duration_s: float
    width: int
    height: int
    codec: str


async def probe(path: Path) -> ProbeResult:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise ProbeError(err.decode(errors="replace").strip() or "ffprobe failed")
    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("no video stream")
    dur = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)
    return ProbeResult(
        duration_s=dur,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        codec=str(video.get("codec_name") or ""),
    )
