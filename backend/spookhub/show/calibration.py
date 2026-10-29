"""Sync calibration clip: black video, white flash + 1 kHz beep every 2 s. Cached via ffmpeg."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from spookhub.models import LightState
from spookhub.show.resolve import CueSpec

CLIP_NAME = "calibration-v1.mp4"
CLIP_S = 20
FLASH_S = 0.1
FLASH_TIMES = tuple(range(0, CLIP_S, 2))

_lock = asyncio.Lock()


def calibration_cues() -> list[CueSpec]:
    white = LightState(on=True, bri=255, colors=[(255, 255, 255)])
    return [
        CueSpec(id=i + 1, start=float(t), duration=FLASH_S, targets=None, state=white)
        for i, t in enumerate(FLASH_TIMES)
    ]


async def ensure_calibration_clip(data_dir: Path) -> Path:
    async with _lock:
        return await _render(data_dir)


async def _render(data_dir: Path) -> Path:
    out = data_dir / CLIP_NAME
    if out.exists():
        return out
    data_dir.mkdir(parents=True, exist_ok=True)
    # One expression gates picture and tone at the start of every even second, so any offset
    # between flash and lights is the lights' alone. 50 fps makes the flash exactly 5 frames.
    on = f"lt(mod(t,2),{FLASH_S})"
    video = (
        f"color=c=black:s=1920x1080:r=50:d={CLIP_S},"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='{on}'"
    )
    audio = f"aevalsrc='if({on},0.8*sin(2*PI*1000*t),0)':s=48000:d={CLIP_S}"
    # Unique tmp per render: a crashed or concurrent run never shares a half-written file.
    tmp = out.with_name(f".{out.stem}.{os.getpid()}.{uuid.uuid4().hex}.tmp.mp4")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-g", "50",
        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(tmp),
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    _, err = await proc.communicate()
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(err.decode(errors="replace")[-300:])
    tmp.replace(out)
    return out
