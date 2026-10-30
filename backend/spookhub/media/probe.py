"""ffprobe wrapper."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProbeError(Exception):
    pass


@dataclass
class ProbeResult:
    duration_s: float
    width: int
    height: int
    codec: str
    # Clockwise degrees the player turns the coded frame (0/90/180/270); width/height stay coded.
    rotation: int = 0
    kind: str = "video"  # 'audio' when there is no real picture stream


def rotation_of(stream: dict[str, Any]) -> int:
    """Display rotation, clockwise. The display matrix `rotation` is counter-clockwise (ffmpeg
    convention, -90 = turn right); the legacy `rotate` tag is already clockwise."""
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return round(-float(sd["rotation"]) / 90) * 90 % 360
            except (TypeError, ValueError):
                break
    try:
        return round(float((stream.get("tags") or {}).get("rotate", 0)) / 90) * 90 % 360
    except (TypeError, ValueError):
        return 0


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
    streams = data.get("streams", [])
    # Cover art in mp3/m4a shows up as a one-frame "video" stream; it is not a picture to play.
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if audio is None:
            raise ProbeError("no video or audio stream")
        dur = float(data.get("format", {}).get("duration") or audio.get("duration") or 0.0)
        return ProbeResult(
            duration_s=dur, width=0, height=0, codec=str(audio.get("codec_name") or ""),
            kind="audio",
        )  # fmt: skip
    dur = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)
    return ProbeResult(
        duration_s=dur,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        codec=str(video.get("codec_name") or ""),
        rotation=rotation_of(video),
    )


async def backfill_rotation(on_change: Callable[[], None] | None = None) -> int:
    """Probe rows that predate the rotation column (rotation NULL). Unreadable files get 0 so
    they are not probed on every start. Returns how many rows changed to a non-zero value."""
    from sqlmodel import col, select

    from spookhub import db
    from spookhub.models import Media

    with db.session() as s:
        todo = [(m.id, m.path) for m in s.exec(select(Media).where(col(Media.rotation).is_(None)))]
    changed = 0
    for mid, path in todo:
        try:
            rot = (await probe(Path(path))).rotation
        except (ProbeError, OSError, ValueError):
            rot = 0
        with db.session() as s:
            m = s.get(Media, mid)
            if m is None:
                continue
            m.rotation = rot
            s.add(m)
            s.commit()
        changed += rot != 0
    if changed and on_change:
        on_change()
    return changed
